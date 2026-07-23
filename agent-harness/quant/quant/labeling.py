"""Path-aware trade labeling + exit quality (Q6).

Triple-barrier (López de Prado): the principled, volatility-scaled exit/label —
each entry gets an upper (profit-take), lower (stop), and vertical (time) barrier;
the FIRST touched defines the outcome. Path-aware (unlike fixed-horizon return
labels, which can call a trade a winner even though it hit the stop first) and the
ground-truth labels for meta-labeling.

Exit efficiency = realized_R / MFE_R — what fraction of the maximum favorable
excursion the exit actually captured. The exit, not the entry, owns the return
ceiling: a perfect entry with 0.4 efficiency underperforms a mediocre one at 0.8.

Pure stdlib, same bar-dict schema as the rest of the package.
"""

from __future__ import annotations

from typing import Sequence

from quant.features import closes, highs, lows


def triple_barrier(bars: Sequence[dict], entries: Sequence[int], *, pt: float = 2.0,
                   sl: float = 1.0, max_bars: int = 20, sigma=None) -> list[dict]:
    """López de Prado triple-barrier labels for each entry bar index.

    Barriers (volatility-scaled): upper = close·(1 + pt·σ_i), lower = close·(1 −
    sl·σ_i), vertical = ``max_bars`` ahead. Scan forward; the STOP is checked
    first within a bar (conservative). Label +1 (profit-take), −1 (stop), or for a
    vertical-barrier timeout the sign of the holding return. ``sigma`` = per-bar
    volatility FRACTION series (e.g. realized_vol or atr/close); None → flat 1%.
    Returns ``{entry_idx, exit_idx, label, touched, ret}`` per entry."""
    c, h, low_ = closes(bars), highs(bars), lows(bars)
    n = len(bars)
    out: list[dict] = []
    for i in entries:
        if i < 0 or i >= n:
            continue
        s = sigma[i] if (sigma and i < len(sigma) and sigma[i] is not None) else 0.01
        up, dn = c[i] * (1 + pt * s), c[i] * (1 - sl * s)
        label, exit_idx, touched = 0, min(i + max_bars, n - 1), "vertical"
        for j in range(i + 1, min(i + max_bars + 1, n)):
            if low_[j] <= dn:                  # stop first (pessimistic / honest)
                label, exit_idx, touched = -1, j, "sl"
                break
            if h[j] >= up:
                label, exit_idx, touched = 1, j, "pt"
                break
        if touched == "vertical":
            label = 1 if c[exit_idx] > c[i] else (-1 if c[exit_idx] < c[i] else 0)
        out.append({"entry_idx": i, "exit_idx": exit_idx, "label": label,
                    "touched": touched, "ret": (c[exit_idx] / c[i] - 1.0) if c[i] else 0.0})
    return out


def max_favorable_excursion(bars: Sequence[dict], entry_ts: int, exit_ts: int, *,
                            direction: str = "long") -> float | None:
    """Best price reached over [entry_ts, exit_ts] — max high (long) / min low
    (short). None if no bars fall in the window."""
    seg = [b for b in bars if entry_ts <= int(b.get("bucket_ts", 0)) <= exit_ts]
    if not seg:
        return None
    return (max(float(b["high"]) for b in seg) if direction == "long"
            else min(float(b["low"]) for b in seg))


def exit_efficiency(entry: float, exit_: float, mfe: float, *, direction: str = "long") -> float | None:
    """realized_R / MFE_R (≤ 1). 1.0 = exited the exact peak; <0 = exited red while
    the trade had gone green. None if the trade never went favorable (undefined)."""
    if direction == "long":
        fav, realized = mfe - entry, exit_ - entry
    else:
        fav, realized = entry - mfe, entry - exit_
    return realized / fav if fav > 0 else None


def mean_exit_efficiency(trades: Sequence, bars: Sequence[dict]) -> float | None:
    """Average exit efficiency across long trades (each needs entry/exit/entry_ts/
    exit_ts). Trades that never went favorable are skipped."""
    effs = []
    for t in trades:
        mfe = max_favorable_excursion(bars, int(t.entry_ts), int(t.exit_ts), direction="long")
        if mfe is None:
            continue
        e = exit_efficiency(float(t.entry), float(t.exit), mfe, direction="long")
        if e is not None:
            effs.append(e)
    return sum(effs) / len(effs) if effs else None
