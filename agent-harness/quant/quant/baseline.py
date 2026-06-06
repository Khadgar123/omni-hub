"""Directionless BASELINE strategy — cross-sectional momentum × vol-target × regime gate.

This is the product FLOOR: fully automatic, NO human direction (price-direction is a
~0.59 wall, so we never bet it). We bet two things that ARE measurable:
  * relative strength — long the strongest coins, short the weakest (dollar-neutral,
    β≈0); the Liu-Tsyvinski crypto momentum factor (1–4-week persistence);
  * volatility — predictable (vol clusters, IC≈0.6 vs direction's ≈0.04), used to
    size: position ∝ target_vol / realized_vol, and cut exposure when MARKET vol spikes
    (where momentum crashes from short-leg squeezes live).

The default config is the chosen "最佳平衡": cross-sectional × vol-target 0.40 ×
regime-gate → backtest CAGR ≈ +49%, maxDD ≈ −50%, Sharpe ≈ 1.42, β≈0. vol-target at
0.40 cuts drawdown for FREE (same CAGR as un-targeted, −13pts DD); the gate adds Sharpe.

Pure-stdlib core takes a ``{symbol: {day:int -> close}}`` price map (injectable, so the
backtest + daily decision are unit-testable with no store/network). NEVER places an
order — ``daily_decision`` is a basket to be sized by you / emitted as an intent.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field

# the liquid majors used as the cross-sectional universe (override via config/CLI)
DEFAULT_UNIVERSE = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
                    "AVAXUSDT", "DOGEUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "ATOMUSDT",
                    "UNIUSDT", "MATICUSDT"]


@dataclass(slots=True)
class BaselineConfig:
    lookback: int = 14          # momentum signal horizon (days)
    k: int = 3                  # longs = top-k, shorts = bottom-k
    rebalance: int = 1          # days between re-ranks
    target_vol: float = 0.40    # annualized strategy vol target (0.40 = free DD cut)
    vol_cap: float = 3.0        # max position multiplier from vol-target
    vol_lookback: int = 20      # trailing window for strategy realized vol
    gate_mult: float = 1.5      # market vol > gate_mult × trailing median ⇒ high-vol regime
    gate_scale: float = 0.4     # exposure multiplier in the high-vol regime
    gate_vol_lookback: int = 20
    gate_min_history: int = 60  # need this many days before the gate activates
    cost: float = 0.0006        # per-rebalance turnover cost
    periods_per_year: int = 365


@dataclass(slots=True)
class BasketDecision:
    asof: int
    longs: list                 # [(symbol, momentum_score)] strongest
    shorts: list                # [(symbol, momentum_score)] weakest
    gross_scale: float          # vol-target × gate multiplier to apply now
    regime: str                 # "normal" | "high_vol"
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class BacktestResult:
    cagr: float
    max_drawdown: float
    sharpe: float
    calmar: float
    beta: float
    n_days: int
    final_equity: float

    def to_dict(self) -> dict:
        return asdict(self)


def _ret(prices, s, d0, d1):
    a, b = prices[s].get(d0), prices[s].get(d1)
    return (b / a - 1) if (a and b and a > 0) else None


def momentum_scores(prices: dict, asof: int, lookback_day: int) -> list:
    """``score[s] = close[s][asof] / close[s][asof-lookback] - 1`` (relative strength),
    for symbols with both endpoints. Returns ``[(symbol, score)]`` sorted strongest-first."""
    out = []
    for s in prices:
        a, b = prices[s].get(asof - lookback_day), prices[s].get(asof)
        if a and b and a > 0:
            out.append((s, b / a - 1))
    out.sort(key=lambda x: -x[1])
    return out


def select_basket(scores: list, k: int):
    """Top-k longs / bottom-k shorts from strongest-first ``scores``."""
    if len(scores) < 2 * k:
        return [], []
    return scores[:k], scores[-k:]


def _scale(strat: list, mkt: list, cfg: BaselineConfig) -> tuple:
    """Position multiplier now: vol-target (target/realized) then × gate if MARKET vol
    spikes above its trailing median. Returns (scale, regime)."""
    scale = 1.0
    if len(strat) >= cfg.vol_lookback:
        rv = statistics.pstdev(strat[-cfg.vol_lookback:]) * math.sqrt(cfg.periods_per_year) or 0.01
        scale = min(cfg.vol_cap, cfg.target_vol / rv)
    regime = "normal"
    if len(mkt) >= cfg.gate_min_history:
        mv = statistics.pstdev(mkt[-cfg.gate_vol_lookback:])
        meds = [statistics.pstdev(mkt[j - cfg.gate_vol_lookback:j])
                for j in range(2 * cfg.gate_vol_lookback, len(mkt), 5)]
        med = statistics.median(meds) if meds else mv
        if mv > cfg.gate_mult * med:
            scale *= cfg.gate_scale
            regime = "high_vol"
    return scale, regime


def _simulate(prices: dict, cfg: BaselineConfig) -> dict:
    """Causal walk-forward: score on past [t-L, t], realize t→t+1; size by trailing
    vol/gate (no look-ahead). Returns curve + per-day scaled returns + market returns."""
    days = sorted(set().union(*[set(d) for d in prices.values()])) if prices else []
    eq = 1.0
    curve = [1.0]
    strat: list = []
    scaled: list = []
    mkt: list = []
    held = None
    hl = 0
    syms = list(prices)
    for ti in range(cfg.lookback, len(days) - 1):
        d, dn = days[ti], days[ti + 1]
        mk = [r for r in (_ret(prices, s, d, dn) for s in syms) if r is not None]
        mkt_r = statistics.fmean(mk) if mk else 0.0
        if hl <= 0:
            sc = [(s, v) for s, v in momentum_scores(prices, d, cfg.lookback) if prices[s].get(dn)]
            longs, shorts = select_basket(sc, cfg.k)
            if longs:
                held = (longs, shorts)
                eq *= (1 - cfg.cost * (1.0 if not strat else 0.7))
                hl = cfg.rebalance
        if held:
            lr = [r for r in (_ret(prices, s, d, dn) for s, _ in held[0]) if r is not None]
            sr = [r for r in (_ret(prices, s, d, dn) for s, _ in held[1]) if r is not None]
            r = (statistics.fmean(lr) if lr else 0.0) - (statistics.fmean(sr) if sr else 0.0)
        else:
            r = 0.0
        scale, _ = _scale(strat, mkt, cfg)
        eq *= (1 + scale * r)
        curve.append(eq)
        strat.append(r)
        scaled.append(scale * r)
        mkt.append(mkt_r)
        hl -= 1
    return {"curve": curve, "strat": strat, "scaled": scaled, "mkt": mkt}


def backtest(prices: dict, cfg: BaselineConfig = BaselineConfig()) -> BacktestResult:
    sim = _simulate(prices, cfg)
    curve, scaled, mkt = sim["curve"], sim["scaled"], sim["mkt"]
    if not scaled:
        return BacktestResult(0, 0, 0, 0, 0, 0, 1.0)
    yrs = len(scaled) / cfg.periods_per_year
    eq = curve[-1]
    sh = (statistics.fmean(scaled) / (statistics.pstdev(scaled) or 1)) * math.sqrt(cfg.periods_per_year)
    cagr = eq ** (1 / yrs) - 1 if eq > 0 else -1
    peak = 1.0
    mdd = 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    mm = statistics.fmean(mkt)
    pr = statistics.fmean(scaled)
    cov = statistics.fmean([(scaled[i] - pr) * (mkt[i] - mm) for i in range(len(scaled))])
    beta = cov / (statistics.pvariance(mkt) or 1)
    return BacktestResult(round(cagr, 4), round(mdd, 4), round(sh, 3), round(calmar, 3),
                          round(beta, 3), len(scaled), round(eq, 4))


def daily_decision(prices: dict, cfg: BaselineConfig = BaselineConfig(), asof: int | None = None) -> BasketDecision:
    """Today's basket to hold (longs/shorts) + the exposure multiplier to apply, from
    trailing vol/regime. No look-ahead — uses only data up to ``asof`` (latest day)."""
    days = sorted(set().union(*[set(d) for d in prices.values()])) if prices else []
    if not days:
        return BasketDecision(asof=0, longs=[], shorts=[], gross_scale=0.0, regime="none", note="no data")
    if asof is None:                                     # latest day with a well-populated universe
        asof = next((d for d in reversed(days)
                     if sum(1 for s in prices if prices[s].get(d) and prices[s].get(d - cfg.lookback)) >= 2 * cfg.k),
                    days[-1])
    sim = _simulate({s: {d: v for d, v in prices[s].items() if d <= asof} for s in prices}, cfg)
    scale, regime = _scale(sim["strat"], sim["mkt"], cfg)
    sc = momentum_scores(prices, asof, cfg.lookback)
    longs, shorts = select_basket(sc, cfg.k)
    note = (f"多{[s for s, _ in longs]} / 空{[s for s, _ in shorts]} ×{scale:.2f}仓位"
            f"（{regime}）— 赌相对强弱，非方向") if longs else "样本不足"
    return BasketDecision(asof=asof, longs=[(s, round(v, 4)) for s, v in longs],
                          shorts=[(s, round(v, 4)) for s, v in shorts],
                          gross_scale=round(scale, 3), regime=regime, note=note)


def render_decision(dec: BasketDecision) -> str:
    if not dec.longs:
        return f"[baseline] {dec.note}"
    L = "  ".join(f"{s}({100*v:+.0f}%)" for s, v in dec.longs)
    S = "  ".join(f"{s}({100*v:+.0f}%)" for s, v in dec.shorts)
    return (f"━━ 无方向 baseline 篮子（截面动量×vol-target×regime门，β≈0）\n"
            f"  做多(最强): {L}\n  做空(最弱): {S}\n"
            f"  仓位倍数: ×{dec.gross_scale:.2f}   regime: {dec.regime}\n"
            f"  → 赌'强者继续强于弱者'，不赌大盘涨跌；自动、无需人定方向")


def load_live(symbols=None, *, venue="binance", days=400, opener=None) -> dict:
    """Build the price map from live daily candles (via quant.live's injectable fetcher).
    Returns ``{symbol: {day_int: close}}`` keyed by epoch-day."""
    from quant import live as live_mod

    symbols = symbols or DEFAULT_UNIVERSE
    prices: dict = {}
    for s in symbols:
        try:
            bars = live_mod.fetch_candles(s, "1d", venue=venue, opener=opener)
        except Exception:
            continue
        prices[s] = {int(b["bucket_ts"] // 86_400_000_000): float(b["close"]) for b in bars[-days:]}
    return prices


def load_store(symbols=None, *, start="2017-01-01", end="2031-01-01", freq="1d", root=None) -> dict:
    """(1) Build the price map from the local market_store (production data). Best-effort:
    a symbol with no partitions is skipped. Returns ``{symbol: {day_int: close}}``."""
    from quant import market_store as ms

    symbols = symbols or DEFAULT_UNIVERSE
    root = root if root is not None else ms.DEFAULT_ROOT
    prices: dict = {}
    for s in symbols:
        try:
            rows = ms.bars(s, freq, start, end, root=root)
        except Exception:
            continue
        m = {int(r["bucket_ts"] // 86_400_000_000): float(r["close"]) for r in rows if r.get("close")}
        if m:
            prices[s] = m
    return prices


# ---------------------------------------------------------- (3) carry sleeve
def carry_scores(funding: dict, asof: int, window: int = 7) -> dict:
    """Mean recent funding per coin = the carry yield. Positive funding ⇒ perp premium ⇒
    you EARN it by shorting perp / holding spot (delta-neutral)."""
    out = {}
    for s, fmap in funding.items():
        rec = [fmap[d] for d in range(asof - window + 1, asof + 1) if d in fmap]
        if rec:
            out[s] = statistics.fmean(rec)
    return out


def _carry_day(funding: dict, d: int) -> float:
    fr = [funding[s][d] for s in funding if d in funding[s]]
    pos = [f for f in fr if f > 0]                       # harvest only positive-funding coins
    return statistics.fmean(pos) if pos else 0.0


def backtest_combined(prices: dict, funding: dict, cfg: BaselineConfig = BaselineConfig(),
                      carry_weight: float = 0.4) -> dict:
    """Momentum sleeve + delta-neutral CARRY sleeve (corr≈0, so they diversify). Returns
    ``{momentum, carry, combined: (cagr,maxdd,sharpe), corr_mom_carry}``.
    CARRY CAVEAT: this funding-harvest proxy IGNORES basis drift + execution, so its
    standalone Sharpe is inflated — treat it as a small low-vol overlay, not a headline."""
    days = sorted(set().union(*[set(d) for d in prices.values()])) if prices else []
    mom = _simulate(prices, cfg)["scaled"]
    carry = [_carry_day(funding, days[ti]) for ti in range(cfg.lookback, len(days) - 1)]
    m = min(len(mom), len(carry))
    mom, carry = mom[:m], carry[:m]
    comb = [(1 - carry_weight) * mom[i] + carry_weight * carry[i] for i in range(m)]

    def stats(rets):
        if not rets:
            return (0.0, 0.0, 0.0)
        eq = 1.0
        curve = [1.0]
        for r in rets:
            eq *= (1 + r)
            curve.append(eq)
        yrs = len(rets) / cfg.periods_per_year
        sh = (statistics.fmean(rets) / (statistics.pstdev(rets) or 1)) * math.sqrt(cfg.periods_per_year)
        cagr = eq ** (1 / yrs) - 1 if eq > 0 else -1
        peak = 1.0
        mdd = 0.0
        for x in curve:
            peak = max(peak, x)
            mdd = min(mdd, x / peak - 1)
        return (round(cagr, 4), round(mdd, 4), round(sh, 3))

    corr = 0.0
    if m > 2:
        mm, cm = statistics.fmean(mom), statistics.fmean(carry)
        cov = statistics.fmean((mom[i] - mm) * (carry[i] - cm) for i in range(m))
        sx, sy = statistics.pstdev(mom), statistics.pstdev(carry)
        corr = cov / (sx * sy) if sx and sy else 0.0
    return {"momentum": stats(mom), "carry": stats(carry), "combined": stats(comb),
            "corr_mom_carry": round(corr, 3)}


# ------------------------------------------------ (2) basket -> order intent
def basket_to_intent(dec: BasketDecision, equity: float) -> list:
    """Size the basket into per-leg intents: each leg = equity × gross_scale ÷ #legs
    notional, longs=buy / shorts=sell (delta-neutral, Σbuy≈Σsell). Returns
    ``[{symbol, side, notional}]``."""
    n = len(dec.longs) + len(dec.shorts)
    if n == 0 or equity <= 0:
        return []
    per = round(equity * dec.gross_scale / n, 2)
    return ([{"symbol": s, "side": "buy", "notional": per} for s, _ in dec.longs]
            + [{"symbol": s, "side": "sell", "notional": per} for s, _ in dec.shorts])


def emit_basket_intent(dec: BasketDecision, equity: float, path) -> str:
    """(2) Emit the sized basket as a Proposal-ready ``order_intent`` JSONL line. The
    stdlib seam: omni-hub wraps it in ``Proposal(kind='order_intent')`` for approval —
    it does NOT import quant. Writing the line executes NOTHING."""
    import json
    from pathlib import Path

    rec = {"kind": "order_intent", "strategy": "baseline_xsect", "asof": dec.asof,
           "regime": dec.regime, "gross_scale": dec.gross_scale,
           "legs": basket_to_intent(dec, equity)}
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return str(p)


def main(argv=None):
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(prog="quant.baseline", description=__doc__)
    p.add_argument("command", choices=["decide", "backtest"])
    p.add_argument("--source", choices=["live", "store"], default="live", help="data source")
    p.add_argument("--prices-json", default=None, help="{symbol:{day:close}} map (overrides --source)")
    p.add_argument("--lookback", type=int, default=14)
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--target-vol", type=float, default=0.40)
    p.add_argument("--emit", default=None, help="decide: write a sized order_intent JSONL here")
    p.add_argument("--equity", type=float, default=10000.0, help="equity for --emit sizing")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    cfg = BaselineConfig(lookback=args.lookback, k=args.k, target_vol=args.target_vol)
    if args.prices_json:
        raw = json.load(open(args.prices_json))
        prices = {s: {int(d): v for d, v in m.items()} for s, m in raw.items()}
    else:
        prices = load_store() if args.source == "store" else load_live()
    if args.command == "backtest":
        res = backtest(prices, cfg).to_dict()
        json.dump(res, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        dec = daily_decision(prices, cfg)
        if args.emit:
            path = emit_basket_intent(dec, args.equity, args.emit)
            sys.stdout.write(render_decision(dec) + f"\n→ order_intent 已写入 {path}（等审批，不下实盘）\n")
        elif args.json:
            json.dump(dec.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_decision(dec) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
