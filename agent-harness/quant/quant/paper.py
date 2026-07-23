"""Forward (paper) validation — does a FROZEN strategy hold up on unseen data?

The gold-standard test the practitioners insist on: pick/tune a config on history,
*freeze* it at an inception date (``live_from``), then judge it only on data that
arrived AFTER the freeze. A backtested edge that survives forward is real; one that
collapses was overfit. This is walk-forward made persistent and date-anchored.

Design — zero parity drift: a paper test stores only its DEFINITION (a small JSON);
the track is always recomputed by re-running the SAME ``harness.run`` backtest over
the store (the single source of truth), then splitting trades/equity at ``live_from``
into baseline (pre-freeze) vs forward (post-freeze). No bespoke fill or regime logic,
so paper and backtest can never disagree. As the store grows with live data, the
forward window grows — run ``evaluate`` any time to see the latest verdict.

NEVER places an order — paper only (autonomy L0).

CLI:
    python -m quant.paper create --strategy divergence_reversal_v1 --symbol BTCUSDT \
        --inception 2024-01 --live-from 2025-06 [--htf 1d --confirm 4h]
    python -m quant.paper eval   --id divergence_reversal_v1.BTCUSDT.2025-06 [--report R.html]
    python -m quant.paper list
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from quant import market_store
from quant.backtest import metrics as M

_DIR = Path("~/quant/paper").expanduser()
_MIN_FORWARD_TRADES = 5


def _dir(root=None):
    return Path(root).expanduser() if root else _DIR


def _verdict(baseline, forward, n_forward, min_forward=_MIN_FORWARD_TRADES):
    """Honest forward call. baseline/forward are summarize() dicts."""
    if n_forward < min_forward:
        return "insufficient_forward_data"
    fs, fr = forward["sharpe"], forward["total_return"]
    bs = baseline["sharpe"]
    if fs <= 0 or fr <= 0:
        return "fails"                         # forward not profitable / no positive risk-adj return
    if bs <= 0 or fs >= 0.5 * bs:
        return "holds"                         # forward Sharpe >= half the baseline (or baseline was weak)
    return "degrades"                          # positive but materially below baseline


def forward_split(trades, curve, *, live_from_us, equity0, min_forward_trades=_MIN_FORWARD_TRADES,
                  prices_before=None, prices_after=None):
    """Split a backtest result at ``live_from_us`` and summarize both halves.

    Returns a dict with the verdict, trade counts, the ISO boundary, and the
    baseline/forward metric blocks. Pure — unit-testable with synthetic trades
    (each needs ``.entry_ts`` and ``.pnl``) and an equity curve of (ts, equity).
    """
    base_curve = [(ts, e) for ts, e in curve if ts < live_from_us]
    fwd_curve = [(ts, e) for ts, e in curve if ts >= live_from_us]
    base_trades = [t for t in trades if int(t.entry_ts) < live_from_us]
    fwd_trades = [t for t in trades if int(t.entry_ts) >= live_from_us]
    fwd_equity0 = base_curve[-1][1] if base_curve else equity0
    baseline = M.summarize(base_curve, base_trades, equity0=equity0, prices=prices_before)
    forward = M.summarize(fwd_curve, fwd_trades, equity0=fwd_equity0, prices=prices_after)
    return {
        "verdict": _verdict(baseline, forward, len(fwd_trades), min_forward_trades),
        "n_baseline_trades": len(base_trades),
        "n_forward_trades": len(fwd_trades),
        "live_from": market_store.micros_to_iso(live_from_us),
        "baseline": baseline,
        "forward": forward,
    }


@dataclass(slots=True)
class PaperTest:
    id: str
    strategy_id: str
    symbol: str
    inception: str            # baseline window start (YYYY-MM[-DD])
    live_from: str            # freeze date — forward judged on data at/after this
    htf: str = "1d"
    confirm: str = "4h"
    equity0: float = 10000.0
    source: str = "1s"
    note: str = ""
    created: str = ""
    schema_version: str = "paper-v1"

    def to_dict(self):
        return asdict(self)


def _slug(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))


def create(strategy_id, symbol, *, live_from, inception, htf="1d", confirm="4h",
           equity0=10000.0, source="1s", note="", now=None, root=None):
    """Define + persist a paper test. id = ``<strategy>.<symbol>.<live_from>``."""
    tid = f"{_slug(strategy_id)}.{_slug(symbol)}.{_slug(live_from)}"
    created = (now or datetime.now(UTC)).isoformat()
    test = PaperTest(id=tid, strategy_id=strategy_id, symbol=symbol, inception=inception,
                     live_from=live_from, htf=htf, confirm=confirm, equity0=float(equity0),
                     source=source, note=note, created=created)
    save(test, root=root)
    return test


def save(test, *, root=None):
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{test.id}.json"
    p.write_text(json.dumps(test.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load(test_id, *, root=None):
    p = _dir(root) / f"{test_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"no paper test {test_id!r} in {_dir(root)}")
    return PaperTest(**json.loads(p.read_text(encoding="utf-8")))


def list_tests(*, root=None):
    d = _dir(root)
    if not d.exists():
        return []
    return [PaperTest(**json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(d.glob("*.json")) if p.is_file()]


def evaluate(test, *, root=None, as_of=None, report_path=None):
    """Re-run the backtest over [inception, as_of] and split at live_from.

    Returns the forward/baseline verdict dict plus full-run metrics. Writes an
    HTML report with the live_from boundary marked when ``report_path`` is given.
    """
    from quant.backtest import harness
    store_root = Path(root).expanduser() if root else None
    res, m = harness.run(test.strategy_id, test.symbol, root=store_root, start=test.inception,
                         end=as_of, htf=test.htf, confirm=test.confirm, equity0=test.equity0,
                         source=test.source, report_path=report_path, live_from=test.live_from)
    if res is None:
        return {"id": test.id, "error": m.get("error", "no data")}
    live_us = market_store.parse_ts(test.live_from)
    split = forward_split(res.trades, res.equity_curve, live_from_us=live_us, equity0=test.equity0)
    out = {"id": test.id, "strategy": test.strategy_id, "symbol": test.symbol,
           "timeframe": m.get("timeframe"), "as_of": as_of, "inception": test.inception,
           "bars": m.get("bars"), "full_return": m.get("total_return"), **split}
    if report_path:
        out["report"] = str(report_path)
    return out


def main(argv=None):
    import sys

    p = argparse.ArgumentParser(prog="quant.paper", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="define + persist a paper test")
    c.add_argument("--strategy", required=True)
    c.add_argument("--symbol", default="BTCUSDT")
    c.add_argument("--inception", required=True, help="baseline window start, e.g. 2024-01")
    c.add_argument("--live-from", dest="live_from", required=True, help="freeze date, e.g. 2025-06")
    c.add_argument("--htf", default="1d")
    c.add_argument("--confirm", default="4h")
    c.add_argument("--equity0", type=float, default=10000.0)
    c.add_argument("--note", default="")
    c.add_argument("--dir", default=None, help="paper-test dir (default ~/quant/paper)")

    e = sub.add_parser("eval", help="recompute forward/baseline verdict for a test")
    e.add_argument("--id", required=True)
    e.add_argument("--root", default=None, help="market store root")
    e.add_argument("--as-of", dest="as_of", default=None)
    e.add_argument("--report", default=None, help="write HTML report (live_from marked)")
    e.add_argument("--dir", default=None)

    ls = sub.add_parser("list", help="list paper tests")
    ls.add_argument("--dir", default=None)

    a = p.parse_args(argv)
    if a.cmd == "create":
        t = create(a.strategy, a.symbol, live_from=a.live_from, inception=a.inception, htf=a.htf,
                   confirm=a.confirm, equity0=a.equity0, note=a.note, root=a.dir)
        out = t.to_dict()
    elif a.cmd == "eval":
        t = load(a.id, root=a.dir)
        report_path = Path(a.report).expanduser() if a.report else None
        out = evaluate(t, root=a.root, as_of=a.as_of, report_path=report_path)
    else:
        out = {"tests": [t.to_dict() for t in list_tests(root=a.dir)]}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
