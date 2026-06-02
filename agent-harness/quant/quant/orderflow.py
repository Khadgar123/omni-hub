"""Order-flow primitives — REAL taker-delta / CVD from venue klines.

Binance fapi klines carry ``takerBuyBaseAssetVolume`` per bar (mapped to ``taker_buy`` in
``quant.live.binance_klines``), so we can measure AGGRESSION directly:
``delta = taker_buy - taker_sell = 2*taker_buy - volume`` — who lifts the ask vs hits the
bid. This is the one read that sees *who is winning* rather than price shape, and it's the
upgrade over the OHLCV tick-rule proxy (``quant.mtf.cvd_proxy``).

Honest framing: order-flow is a REAL-TIME CONFIRMATION of aggression, NOT a prediction of
the next bar. Bars without ``taker_buy`` (Coinbase/Kraken/stored bars) fall back to the
close-to-close tick-rule proxy, flagged via ``real=False``. Pure-stdlib.
"""
from __future__ import annotations

from typing import Sequence


def has_real(bars: Sequence[dict]) -> bool:
    """True if any bar carries a real taker-buy volume (venue-provided aggressor split)."""
    return any(b.get("taker_buy") is not None for b in bars)


def taker_delta(bars: Sequence[dict]) -> list[float]:
    """Per-bar aggressor delta (taker buy − taker sell).

    Real when ``taker_buy`` is present (= 2·taker_buy − volume); otherwise a close-to-close
    tick-rule proxy (sign of the bar return × volume)."""
    out: list[float] = []
    prev_close: float | None = None
    for b in bars:
        tb = b.get("taker_buy")
        vol = float(b.get("volume", 0.0) or 0.0)
        if tb is not None:
            out.append(2.0 * float(tb) - vol)
        else:
            c = float(b["close"])
            out.append(0.0 if prev_close is None else (vol if c > prev_close else (-vol if c < prev_close else 0.0)))
        prev_close = float(b["close"])
    return out


def cvd(bars: Sequence[dict]) -> list[float]:
    """Cumulative volume delta — running sum of ``taker_delta`` (the order-flow curve)."""
    run = 0.0
    out: list[float] = []
    for x in taker_delta(bars):
        run += x
        out.append(run)
    return out


def read(bars: Sequence[dict], *, lookback: int = 20) -> dict:
    """Current order-flow read over the last ``lookback`` bars (causal).

    Returns net recent aggression (``delta_recent``), its sign (``flow``), and a
    price-vs-CVD **divergence / absorption** flag — price up while flow is down (or vice
    versa) means the move is *not* flow-backed (a tell of absorption / exhaustion).
    """
    n = len(bars)
    real = has_real(bars)
    if n < 3:
        return {"real": real, "cvd": 0.0, "delta_recent": 0.0, "flow": "flat",
                "divergence": None, "window": 0, "note": "insufficient"}
    closes = [float(b["close"]) for b in bars]
    cv = cvd(bars)
    w = min(lookback, n - 1)
    delta_recent = cv[-1] - cv[-1 - w]
    price_chg = closes[-1] - closes[-1 - w]
    divergence = None
    if price_chg > 0 and delta_recent < 0:
        divergence = "bearish(价涨但主动卖占优=无量/吸筹)"
    elif price_chg < 0 and delta_recent > 0:
        divergence = "bullish(价跌但主动买占优=有人在吸)"
    flow = "buy" if delta_recent > 0 else ("sell" if delta_recent < 0 else "flat")
    return {"real": real, "cvd": round(cv[-1], 2), "delta_recent": round(delta_recent, 2),
            "flow": flow, "divergence": divergence, "window": w}


def absorption_at(bars: Sequence[dict], level: float, *, tol_pct: float = 0.004, window: int = 36) -> str:
    """Did aggression get ABSORBED at ``level`` (heavy taker volume, price held)? Causal.

    Combines net taker-delta of recent bars trading within ``tol_pct`` of the level with where
    price sits now:
      ``defended_support``    — sold into it (delta<0) but price held above ⇒ bids absorbing
      ``defended_resistance`` — bought into it (delta>0) but price held below ⇒ asks absorbing
      ``broke_down`` / ``broke_up`` — price closed decisively through the level
      ``none``                — no real interaction / unclear
    """
    if not bars or level <= 0:
        return "none"
    now = float(bars[-1]["close"])
    far = (now - level) / level
    if far < -2 * tol_pct:
        return "broke_down"
    if far > 2 * tol_pct:
        return "broke_up"
    seg = [b for b in bars[-window:] if abs(float(b["close"]) - level) / level <= tol_pct]
    if len(seg) < 3:
        return "none"
    d = sum(taker_delta(seg))
    if now >= level and d < 0:
        return "defended_support"
    if now <= level and d > 0:
        return "defended_resistance"
    return "none"
