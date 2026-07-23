#!/usr/bin/env python3
"""AKShare A-stock query helper.

Free, no-token replacement for TuShare. Covers A-shares + HK + US + macro
via aggregation over Eastmoney / Sina / SSE / SZSE public endpoints.

Install once (heavy deps: pandas + lxml + bs4):

    pip install akshare
    # or in the project's quant env:
    /Users/hzh/opt/anaconda3/envs/quant/bin/python3.12 -m pip install akshare

Subcommands::

    hist     symbol [--days N]           # daily kline (front-adjusted)
    info     symbol                       # name + latest OHLCV (overseas-friendly)
    news     symbol [--limit N]           # market news (Caixin), filter mentions of symbol
    sector                                # real-time sector quotes
    macro    --indicator {cpi,pmi,gdp,m2} # China macro indicators
    search   keyword                      # search stock by name/code

Overseas-IP note:
    All commands here use endpoints that work from US / EU / JP proxy
    nodes (Sina, Caixin, SSE/SZSE list, AKShare macros).  Eastmoney
    push2 / Xueqiu API endpoints are NOT used — they're hard-walled
    against overseas IPs.  No need to disable HTTPS_PROXY beforehand;
    the script auto-adds NO_PROXY entries for mainland data hosts.

Output: JSON (one object per row); orient="records".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

# AKShare bulk-list endpoints (stock_info_a_code_name etc.) emit tqdm
# progress bars to stderr; silence them so callers piping stdout don't
# see "16 batches" noise.  Set before akshare import path triggers.
os.environ.setdefault("TQDM_DISABLE", "1")

# Bypass overseas HTTPS_PROXY for China-mainland data hosts.  AKShare
# calls eastmoney / sina / sse / szse / stats.gov.cn / pbc.gov.cn — if
# the user has HTTPS_PROXY pointing to a US/JP node (Clash/V2Ray with
# overseas selectors), routing those requests overseas triggers
# anti-bot checks and frequent connection resets.  NO_PROXY suffix
# match short-circuits requests/urllib3 so these hosts go direct
# regardless of any HTTPS_PROXY in the environment.
_CN_BYPASS_HOSTS = ",".join([
    "eastmoney.com", "push2.eastmoney.com", "datacenter-web.eastmoney.com",
    "sina.com.cn", "finance.sina.com.cn", "vip.stock.finance.sina.com.cn",
    "10jqka.com.cn", "basic.10jqka.com.cn",
    "sse.com.cn", "szse.cn",
    "gov.cn", "stats.gov.cn", "pbc.gov.cn",
])
_existing = os.environ.get("NO_PROXY", os.environ.get("no_proxy", "")).strip(",")
_combined = (_existing + "," + _CN_BYPASS_HOSTS).strip(",") if _existing else _CN_BYPASS_HOSTS
os.environ["NO_PROXY"] = _combined
os.environ["no_proxy"] = _combined


def _need_akshare():
    try:
        import akshare as ak                                       # noqa: F401
        return ak
    except ImportError:
        sys.stderr.write(
            "akshare not installed. Install with:\n"
            "  pip install akshare\n"
            "or in this project's conda env:\n"
            "  /Users/hzh/opt/anaconda3/envs/quant/bin/python3.12 -m pip install akshare\n"
        )
        sys.exit(2)


def _df_to_json(df, limit=None):
    if limit is not None:
        df = df.tail(limit)
    return df.to_json(orient="records", date_format="iso", force_ascii=False)


def _prefixed_symbol(code: str) -> str:
    """6-digit A-share code → exchange-prefixed (sh / sz / bj)."""

    if code.startswith(("60", "68", "90")):
        return "sh" + code
    if code.startswith(("00", "20", "30")):
        return "sz" + code
    if code.startswith(("43", "83", "87", "88", "92")):
        return "bj" + code
    return "sh" + code                                             # default


def cmd_hist(args):
    ak = _need_akshare()
    sym_pref = _prefixed_symbol(args.symbol)
    end = date.today()
    start = end - timedelta(days=args.days * 2 + 14)

    # Source ladder — overseas-friendly first, Eastmoney push2 last.
    # All three return the same logical data (daily OHLCV); columns
    # differ so each branch normalises to a common JSON shape.
    last_err: Exception | None = None

    # 1. stock_zh_a_daily — Sina/Xueqiu aggregation, widest fields,
    #    most reliable from overseas IPs.
    try:
        df = ak.stock_zh_a_daily(symbol=sym_pref, adjust="qfq")
        df = df.tail(args.days)
        print(df.to_json(orient="records", date_format="iso", force_ascii=False))
        return
    except Exception as exc:                                       # noqa: BLE001
        last_err = exc

    # 2. stock_zh_a_hist_tx — Tencent, lighter response, good fallback.
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=sym_pref,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        df = df.tail(args.days)
        print(df.to_json(orient="records", date_format="iso", force_ascii=False))
        return
    except Exception as exc:                                       # noqa: BLE001
        last_err = exc

    # 3. stock_zh_a_hist — Eastmoney, last resort (often blocked overseas).
    try:
        df = ak.stock_zh_a_hist(
            symbol=args.symbol, period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        print(_df_to_json(df, limit=args.days))
        return
    except Exception as exc:                                       # noqa: BLE001
        last_err = exc

    sys.stderr.write(
        f"hist failed across all 3 sources (sina/tencent/eastmoney): "
        f"{type(last_err).__name__}: {last_err}\n"
    )
    sys.exit(3)


def cmd_info(args):
    ak = _need_akshare()
    # Same overseas-friendly source as cmd_hist: stock_zh_a_daily
    # (Sina/Xueqiu) returns OHLCV + outstanding_share + turnover with
    # English columns.  Falls back through Tencent if Sina hiccups.
    sym_pref = _prefixed_symbol(args.symbol)
    df = None
    for fn, kwargs in (
        (ak.stock_zh_a_daily, {"symbol": sym_pref, "adjust": "qfq"}),
        (ak.stock_zh_a_hist_tx, {
            "symbol": sym_pref,
            "start_date": (date.today() - timedelta(days=14)).strftime("%Y%m%d"),
            "end_date": date.today().strftime("%Y%m%d"),
        }),
    ):
        try:
            df = fn(**kwargs)
            break
        except Exception:                                          # noqa: BLE001
            continue
    if df is None or df.empty:
        sys.stderr.write(f"no trading data for {args.symbol}\n")
        sys.exit(3)

    latest = df.iloc[-1]
    name = _lookup_name_best_effort(ak, args.symbol)

    info: dict[str, object] = {
        "symbol": args.symbol,
        "name": name,
        "latest_date": str(latest["date"])[:10],
        "open": float(latest["open"]),
        "close": float(latest["close"]),
        "high": float(latest["high"]),
        "low": float(latest["low"]),
    }
    # Sina/Xueqiu adds these; Tencent fallback won't have them.
    for col in ("volume", "amount", "outstanding_share", "turnover"):
        if col in df.columns:
            try:
                info[col] = float(latest[col])
            except (TypeError, ValueError):
                pass
    print(json.dumps(info, ensure_ascii=False))


def _lookup_name_best_effort(ak, symbol: str) -> str:
    """Resolve symbol → company name.  Failure-tolerant: returns
    "unknown" rather than raising, since name is non-essential.
    """

    try:
        df_names = ak.stock_info_a_code_name()
        hit = df_names[df_names["code"].astype(str) == symbol]
        return str(hit["name"].iloc[0]) if not hit.empty else "unknown"
    except Exception:                                              # noqa: BLE001
        return "unknown"


def cmd_news(args):
    ak = _need_akshare()
    # Eastmoney's per-symbol news endpoint returns malformed Arrow blobs
    # from overseas IPs.  Caixin's market-wide news feed is the most
    # IP-tolerant alternative; filter post-hoc to anything mentioning
    # the requested symbol (or fall back to top market headlines).
    df = ak.stock_news_main_cx()
    sym = (args.symbol or "").strip()
    if sym and sym.lower() != "all":
        cols_to_scan = [c for c in ("summary", "tag", "title") if c in df.columns]
        if cols_to_scan:
            mask = df[cols_to_scan].astype(str).apply(
                lambda row: sym in " ".join(row.values), axis=1,
            )
            filtered = df[mask]
            if not filtered.empty:
                df = filtered
            # else: fall through with full market feed — better than empty
    print(_df_to_json(df, limit=args.limit))


def cmd_sector(args):
    ak = _need_akshare()
    df = ak.stock_sector_spot()
    print(_df_to_json(df))


_MACRO_DATE_COLS = ("日期", "月份", "季度", "时间", "date")


def _sort_recent(df, n):
    """Return the most-recent N rows.  AKShare's macro endpoints are
    inconsistent (CPI newest-first; PMI oldest-first; etc.).  Detect the
    date-ish column and sort descending so head() always gives the latest.
    """

    for col in _MACRO_DATE_COLS:
        if col in df.columns:
            # ISO ('2005-02-01') and Chinese ('2026年04月份') both sort
            # lexicographically in time order, so str-sort is safe.
            df = df.copy()
            df["_sort"] = df[col].astype(str)
            df = df.sort_values("_sort", ascending=False).drop(columns="_sort")
            break
    return df.head(n)


def cmd_macro(args):
    ak = _need_akshare()
    indicators = {
        "cpi": ak.macro_china_cpi,
        "pmi": ak.macro_china_pmi_yearly,
        "gdp": ak.macro_china_gdp,
        "m2": ak.macro_china_money_supply,
    }
    fn = indicators.get(args.indicator)
    if not fn:
        sys.stderr.write(f"unknown indicator: {args.indicator}\n")
        sys.exit(2)
    df = fn()
    print(_sort_recent(df, args.limit).to_json(
        orient="records", date_format="iso", force_ascii=False,
    ))


def cmd_search(args):
    ak = _need_akshare()
    # stock_info_a_code_name aggregates SSE + SZSE official lists (5500+
    # rows, refreshed weekly).  Much more reliable than Eastmoney spot
    # snapshots, which trip reverse-engineering checks under load.
    df = ak.stock_info_a_code_name()
    hits = df[df["name"].str.contains(args.keyword, na=False) |
              df["code"].astype(str).str.contains(args.keyword, na=False)]
    if hits.empty:
        sys.stderr.write(f"no match for: {args.keyword}\n")
        return
    print(_df_to_json(hits.head(args.limit)))


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp = p.add_subparsers(dest="cmd", required=True)

    h = sp.add_parser("hist", help="daily kline")
    h.add_argument("symbol", help="6-digit A-share code, e.g. 600519")
    h.add_argument("--days", type=int, default=30)
    h.set_defaults(fn=cmd_hist)

    i = sp.add_parser("info", help="individual stock info")
    i.add_argument("symbol")
    i.set_defaults(fn=cmd_info)

    n = sp.add_parser("news", help="recent news")
    n.add_argument("symbol")
    n.add_argument("--limit", type=int, default=10)
    n.set_defaults(fn=cmd_news)

    s = sp.add_parser("sector", help="real-time sector quotes")
    s.set_defaults(fn=cmd_sector)

    m = sp.add_parser("macro", help="China macro indicators")
    m.add_argument("--indicator", choices=["cpi", "pmi", "gdp", "m2"], default="cpi")
    m.add_argument("--limit", type=int, default=12)
    m.set_defaults(fn=cmd_macro)

    sr = sp.add_parser("search", help="search stock by name/code substring")
    sr.add_argument("keyword")
    sr.add_argument("--limit", type=int, default=10)
    sr.set_defaults(fn=cmd_search)

    args = p.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
