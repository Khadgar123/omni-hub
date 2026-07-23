"""Optional ffn (MIT) tear-sheet stats — the ROBUST mature analytics adopt.

The research verdict + an empirical test pick ffn over QuantStats: ffn is MIT,
the most actively maintained tearsheet lib, consumes a PRICE series (matches our
OHLCV store with no returns-conversion seam), and — verified — it computes stats
on the SPARSE / near-flat research equity that makes QuantStats crash (its #334
cagr ZeroDivisionError, closed wontfix). Use it as a second-opinion oracle next to
the pure tearsheet (quant.backtest.tearsheet), which stays the zero-dependency default.

Guarded import (`pip install ffn`). NOT a replacement for the hand-built core
(engine + DSR/PBO validation + self-contained report) — a thin leaf library.
"""

from __future__ import annotations

import datetime as _dt

_KEYS = ("total_return", "daily_sharpe", "daily_sortino", "max_drawdown", "cagr", "calmar")


def ffn_stats(result, *, daily: bool = True) -> dict:
    """ffn ``PerformanceStats`` for a BacktestResult's equity curve → a plain dict
    of headline ratios (None where ffn returns NaN). Raises RuntimeError with an
    install hint if ffn is absent. Robust on sparse/near-flat equity."""
    try:
        import ffn
        import pandas as pd
    except Exception as e:  # pragma: no cover - optional dependency
        raise RuntimeError("ffn not installed — `pip install ffn`") from e
    curve = result.equity_curve
    if len(curve) < 3:
        raise ValueError("need >= 3 equity points")
    idx = pd.DatetimeIndex([_dt.datetime.fromtimestamp(int(ts) / 1e6, _dt.timezone.utc).replace(tzinfo=None)
                            for ts, _ in curve])
    eq = pd.Series([float(e) for _, e in curve], index=idx)
    eq = eq[~eq.index.duplicated(keep="last")].sort_index()
    if daily:
        eq = eq.resample("1D").last().ffill()
    st = ffn.core.PerformanceStats(eq).stats
    return {k: (float(st[k]) if (k in st.index and st[k] == st[k]) else None) for k in _KEYS}
