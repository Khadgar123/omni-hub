"""Analog matching — find the top-K most similar HISTORICAL windows to a query window and project
the analog-ensemble forward path.

Core = MASS (Mueen's Algorithm for Similarity Search): the z-normalized Euclidean distance profile
of a query against every length-m window of a series, in O(n log n) via FFT. This is the matrix-
profile primitive, implemented in numpy (no stumpy/numba dependency).

Why z-normalized: matches the SHAPE (capitulation -> wick -> bounce -> second-high) regardless of
absolute price level. Why cascade: the higher timeframe sets the regime (filter), the lower
timeframe refines the tactical setup — 'higher TF = context, lower TF = execution'.

Modes:
  match_level(symbol, tf, win)        per-level top-K (one timeframe on its own terms)
  match_multi(symbol, [(tf,win),..])  cascade: HTF filters candidates, LTF refines (joint distance)
  analog_forward(closes, matches)     ensemble forward = median path + percentile fan (the most
                                      likely forward SHAPE drawn from real history, incl. 2nd highs)
"""
from __future__ import annotations

import numpy as np

from . import market_store as ms


# ---- MASS core -----------------------------------------------------------

def _sliding_dot(q: np.ndarray, ts: np.ndarray) -> np.ndarray:
    """Sliding dot product of query q against ts (length n-m+1), via FFT."""
    n, m = len(ts), len(q)
    size = 1 << (int(np.ceil(np.log2(2 * n))) )            # next pow2 >= 2n
    QT = np.fft.irfft(np.fft.rfft(ts, size) * np.fft.rfft(q[::-1], size), size)
    return QT[m - 1:n]


def _sliding_mean_std(ts: np.ndarray, m: int):
    c = np.concatenate([[0.0], np.cumsum(ts)])
    c2 = np.concatenate([[0.0], np.cumsum(ts * ts)])
    s = c[m:] - c[:-m]
    s2 = c2[m:] - c2[:-m]
    mu = s / m
    var = np.maximum(s2 / m - mu * mu, 0.0)
    return mu, np.sqrt(var)


def dist_profile(query, ts) -> np.ndarray:
    """z-normalized Euclidean distance from ``query`` to every length-m window of ``ts`` (MASS).

    Returns an array of length ``len(ts)-len(query)+1``; lower = more similar SHAPE."""
    q = np.asarray(query, float)
    ts = np.asarray(ts, float)
    m = len(q)
    if len(ts) < m + 1:
        return np.array([])
    mu_q, sig_q = q.mean(), q.std() or 1e-9
    QT = _sliding_dot(q, ts)
    mu_t, sig_t = _sliding_mean_std(ts, m)
    sig_t = np.where(sig_t > 1e-9, sig_t, 1e-9)
    corr = np.clip((QT - m * mu_q * mu_t) / (m * sig_q * sig_t), -1.0, 1.0)
    return np.sqrt(np.maximum(2 * m * (1 - corr), 0.0))


def top_k(query, ts, k: int = 8, excl: int | None = None) -> list[tuple[int, float]]:
    """Top-K non-overlapping best matches: ``[(start_index_in_ts, distance), ...]`` sorted best-first."""
    d = dist_profile(query, ts)
    if d.size == 0:
        return []
    excl = excl if excl is not None else max(1, len(query) // 2)
    picked: list[int] = []
    for i in np.argsort(d):
        i = int(i)
        if all(abs(i - j) >= excl for j in picked):
            picked.append(i)
            if len(picked) >= k:
                break
    return [(p, float(d[p])) for p in picked]


# ---- per-level + forward -------------------------------------------------

def _closes(symbol, tf, start="2019-09-01", end="2100-01-01", bars=None):
    bars = bars if bars is not None else ms.bars(symbol, tf, start, end)
    return bars, np.array([b["close"] for b in bars], float)


def match_level(symbol, tf, win: int, k: int = 8, *, query=None, bars=None) -> dict:
    """Per-level top-K. Query defaults to the LAST ``win`` closes; searched over all earlier history
    (the query's own region is excluded so it can't match itself)."""
    bars, closes = _closes(symbol, tf, bars=bars)
    q = np.asarray(query, float) if query is not None else closes[-win:]
    hist = closes[:-win] if query is None else closes        # exclude the live window when it's the query
    matches = top_k(q, hist, k=k, excl=win)
    return {"symbol": symbol, "tf": tf, "win": win, "n_hist": len(hist),
            "query": q, "closes": closes, "bars": bars, "matches": matches}


def analog_forward(closes, matches, win: int, horizon: int) -> dict:
    """Ensemble forward: for each match, take the next ``horizon`` closes as % from the match's
    'now', then median + percentile fan. This is the most-likely forward SHAPE from real history."""
    closes = np.asarray(closes, float)
    paths = []
    for pos, _dist in matches:
        end = pos + win
        fwd = closes[end:end + horizon]
        if len(fwd) < horizon:
            continue
        anchor = closes[end - 1]
        if anchor > 0:
            paths.append(fwd / anchor - 1.0)               # forward return path
    if not paths:
        return {"n": 0}
    P = np.vstack(paths)
    return {"n": len(paths),
            "median": np.median(P, 0), "p25": np.percentile(P, 25, 0), "p75": np.percentile(P, 75, 0),
            "p10": np.percentile(P, 10, 0), "p90": np.percentile(P, 90, 0)}


# ---- multi-level cascade -------------------------------------------------

def _window_ending_at(bars, closes, ts_end: int, win: int):
    """The length-``win`` close window ending at-or-before bucket_ts ``ts_end``; (start_idx, window)."""
    j = np.searchsorted([b["bucket_ts"] for b in bars], ts_end, side="right") - 1
    if j < win - 1:
        return None, None
    return j - win + 1, closes[j - win + 1: j + 1]


def match_multi(symbol, tfs_wins: list[tuple[str, int]], k: int = 8, horizon: int = 30,
                weights: dict | None = None, htf_candidates: int = 40) -> dict:
    """Cascade match: the FIRST (highest) tf filters candidate moments; lower tfs refine via a
    joint z-normalized distance (weighted). ``tfs_wins`` is ordered HIGH->low, e.g.
    [('1d',30),('4h',60),('30m',96)]. Returns the joint top-K + each tf's forward ensemble."""
    htf, hwin = tfs_wins[0]
    base = match_level(symbol, htf, hwin, k=htf_candidates)
    hbars, hcloses = base["bars"], base["closes"]
    weights = weights or {tf: w for (tf, _), w in zip(tfs_wins, [1.0, 0.6, 0.3, 0.15, 0.1])}

    lower = []
    for tf, win in tfs_wins[1:]:
        b, c = _closes(symbol, tf)
        cur = c[-win:]
        lower.append((tf, win, b, c, (cur - cur.mean()) / (cur.std() or 1e-9)))

    scored = []
    for pos, hdist in base["matches"]:
        ts_end = hbars[pos + hwin - 1]["bucket_ts"]
        joint = weights.get(htf, 1.0) * hdist
        per = {htf: hdist}
        ok = True
        for tf, win, b, c, cur_z in lower:
            _, w = _window_ending_at(b, c, ts_end, win)
            if w is None or len(w) < win:
                ok = False
                break
            wz = (w - w.mean()) / (w.std() or 1e-9)
            dl = float(np.sqrt(np.sum((cur_z - wz) ** 2)))
            per[tf] = dl
            joint += weights.get(tf, 0.3) * dl
        if ok:
            scored.append({"pos": pos, "ts_end": ts_end, "joint": joint, "per": per})
    scored.sort(key=lambda x: x["joint"])
    top = scored[:k]
    fwd = analog_forward(hcloses, [(s["pos"], s["joint"]) for s in top], hwin, horizon)
    return {"symbol": symbol, "tfs_wins": tfs_wins, "htf": htf, "hwin": hwin,
            "bars": hbars, "closes": hcloses, "top": top, "forward": fwd}
