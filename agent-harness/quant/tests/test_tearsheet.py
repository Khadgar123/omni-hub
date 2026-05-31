"""Pure tear-sheet analytics: drawdown, rolling Sharpe, monthly returns, MAE/MFE,
risk scalars."""

import datetime as dt
import random
from types import SimpleNamespace

import pytest

from quant.backtest import tearsheet as T


def _curve(equities, t0=0, step=86_400_000_000):
    return [(t0 + i * step, float(e)) for i, e in enumerate(equities)]


def _ts(y, m, d):
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp() * 1e6)


def test_drawdown_series():
    dd = T.drawdown_series(_curve([100, 110, 99, 120]))
    assert dd[0][1] == 0.0 and dd[1][1] == 0.0
    assert dd[2][1] == pytest.approx((99 - 110) / 110)   # -10% from the 110 peak
    assert dd[3][1] == 0.0                                # new peak -> flat water


def test_rolling_sharpe_warmup_and_values():
    rs = T.rolling_sharpe([0.01] * 30, 10, 365)
    assert rs[8] is None and rs[9] == 0.0                 # constant -> sd 0 -> 0
    random.seed(1)
    r = [0.01 + 0.001 * random.gauss(0, 1) for _ in range(60)]
    assert T.rolling_sharpe(r, 20, 365)[-1] > 0           # positive drift -> +Sharpe


def test_monthly_returns():
    curve = [(_ts(2025, 1, 1), 100.0), (_ts(2025, 1, 31), 100.0),
             (_ts(2025, 2, 15), 105.0), (_ts(2025, 2, 28), 110.0)]
    mr = T.monthly_returns(curve)
    assert set(mr) == {"2025-01", "2025-02"}
    assert mr["2025-02"] == pytest.approx(110 / 100 - 1)  # Feb last / Jan last - 1


def test_mae_mfe_long():
    bars = [{"open": 100, "high": h, "low": low, "close": 100, "volume": 1.0,
             "bucket_ts": i * 60_000_000}
            for i, (h, low) in enumerate([(102, 98), (108, 95), (106, 99)])]
    t = SimpleNamespace(entry_ts=0, exit_ts=120_000_000, entry=100.0, return_pct=0.05, pnl=5.0)
    mm = T.mae_mfe([t], bars)
    assert len(mm) == 1
    assert mm[0]["mfe"] == pytest.approx(108 / 100 - 1)   # +8% best run-up
    assert mm[0]["mae"] == pytest.approx(95 / 100 - 1)    # -5% worst draw
    assert mm[0]["win"] is True


def test_scalar_metrics():
    r = [0.02, -0.01, 0.03, -0.02, 0.01, 0.02, -0.005]
    assert T.sortino(r, 365) > 0
    assert T.value_at_risk(r, 0.3) <= 0
    assert T.cvar(r, 0.3) <= T.value_at_risk(r, 0.3)      # tail mean ≤ the quantile
    assert T.calmar(0.5, -0.2, 1.0) == pytest.approx(0.5 / 0.2)   # CAGR 0.5 / |DD| 0.2
    assert T.ulcer_index(_curve([100, 90, 95, 100])) > 0
