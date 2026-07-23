"""BTC/ETH/SOL spot-ETF daily net flow via Farside (free, no key, T+1 daily).

The real seam for the framework's ``etf`` slot (was a hand-maintained JSON file). Farside publishes
one HTML table of per-issuer daily flows (US$m) plus a trailing ``Total`` column; we parse the latest
REPORTED row. NETWORK lives here (quant venv); omni-hub only shells out — never imports quant.

IMPORTANT — this is a **T+1 DAILY** number: primary-market creation/redemption settles at the close
and is reported the next morning. There is **no truly real-time ETF flow**; for intraday demand use the
ETF's volume / premium-to-NAV (yfinance), not this. Direction is a STATE read, never a forecast.

CLI:  python -m quant.etf [--asset btc|eth|sol] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request

FARSIDE = {
    "btc": "https://farside.co.uk/btc/",
    "eth": "https://farside.co.uk/eth/",
    "sol": "https://farside.co.uk/sol/",
}
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_DATE = re.compile(r"^\d{1,2}\s+\w{3,}\s+\d{4}$")        # "01 Jun 2026"
_TABLE = re.compile(r"<table.*?</table>", re.S | re.I)
_TR = re.compile(r"<tr.*?</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh].*?</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<.*?>", re.S)


def _num(s):
    """Farside cell -> float | None.  '(9.5)'->-9.5, '1,119.9'->1119.9, '-'/''->None."""
    s = (s or "").strip()
    if s in ("", "-", "—"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("$", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _cells(row):
    return [_TAG.sub(" ", c).replace("&nbsp;", " ").strip() for c in _CELL.findall(row)]


def _parse_table(html):
    """Farside HTML -> {asof, net, tickers, by_issuer, history:[(date,net)...]}.  Pure / unit-tested.

    Picks the data table (the one with 'IBIT' + the most rows), reads per-issuer cells + the trailing
    ``Total`` column, returns the LATEST REPORTED day — skipping not-yet-reported rows (every issuer
    cell '-').  Header / fee / summary rows are filtered by the date regex.  Raises on no data."""
    tables = _TABLE.findall(html)
    if not tables:
        raise ValueError("Farside: no <table> found")
    tbl = max(tables, key=lambda t: ("IBIT" in t) * 100000 + len(_TR.findall(t)))
    rows = [_cells(r) for r in _TR.findall(tbl)]
    tickers = next((r[1:-1] for r in rows if "IBIT" in r), [])
    data = []
    for r in rows:
        if len(r) < 3 or not _DATE.match(r[0] or ""):           # only real "DD Mon YYYY" rows
            continue
        issuer_cells = r[1:-1]
        if all(x in ("", "-", "—") for x in issuer_cells):      # day not reported yet
            continue
        flows = [_num(x) for x in issuer_cells]
        net = _num(r[-1])
        if net is None:
            net = sum(f for f in flows if f is not None)
        data.append((r[0], flows, net))
    if not data:
        raise ValueError("Farside: no data rows parsed")
    asof, flows, net = data[-1]
    by_issuer = {t: f for t, f in zip(tickers, flows) if f is not None}
    return {"asof": asof, "net": net, "tickers": tickers,
            "by_issuer": by_issuer, "history": [(d, n) for d, _f, n in data]}


def summarize(parsed, flat_band=20.0):
    """Latest net flow -> framework ``etf`` dict {trend, net_usd_m, streak, last5_sum_usd_m, note, ...}.

    ``flat_band`` US$m: |net| under it reads 'flat'.  ``streak`` = consecutive same-sign days from the end."""
    net = parsed["net"]
    trend = "inflow" if net > flat_band else "outflow" if net < -flat_band else "flat"
    hist = parsed["history"]
    last5 = [n for _d, n in hist[-5:]]
    sign = (net > 0) - (net < 0)
    streak = 0
    for _d, n in reversed(hist):
        s = (n > 0) - (n < 0)
        if s == sign and s != 0:
            streak += 1
        else:
            break
    top = sorted(parsed["by_issuer"].items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]
    word = "流入" if sign > 0 else "流出" if sign < 0 else "走平"
    note = (f"净 {net:+,.0f}M · 5日 {sum(last5):+,.0f}M · 连{streak}日{word} · {parsed['asof']} (Farside T+1)")
    return {"trend": trend, "net_usd_m": round(net, 1), "streak": streak,
            "last5_sum_usd_m": round(sum(last5), 1), "asof": parsed["asof"],
            "top_issuers": [{"ticker": t, "musd": round(v, 1)} for t, v in top],
            "note": note, "source": "farside"}


def _gethtml(url, *, opener=None, timeout=20.0):
    opener = opener or urllib.request.urlopen
    with opener(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch(asset="btc", *, opener=None, timeout=20.0):
    """Live Farside read for ``asset`` -> framework ``etf`` dict (+ ``available``).  NEVER raises:
    on any failure returns ``{available: False, reason, asset, source}`` so a read never breaks."""
    asset = (asset or "btc").lower()
    url = FARSIDE.get(asset)
    if not url:
        return {"available": False, "reason": f"no Farside page for {asset}", "asset": asset, "source": "farside"}
    try:
        out = summarize(_parse_table(_gethtml(url, opener=opener, timeout=timeout)))
        out["available"] = True
        out["asset"] = asset
        return out
    except Exception as e:                                       # network / parse / page-shape change
        return {"available": False, "reason": str(e)[:140], "asset": asset, "source": "farside"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quant.etf", description="Spot-ETF daily net flow (Farside, T+1 daily).")
    p.add_argument("--asset", default="btc", choices=sorted(FARSIDE))
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    r = fetch(a.asset)
    if a.json:
        json.dump(r, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    elif r.get("available"):
        print(f"{a.asset.upper()} spot-ETF: {r['trend']}  {r['note']}")
        for it in r.get("top_issuers", []):
            print(f"   {it['ticker']:>5}  {it['musd']:+,.1f}M")
        print("※ T+1 日度一级申赎净额(Farside);非实时、非投资建议、非涨跌预测。")
    else:
        print(f"{a.asset.upper()} spot-ETF: UNAVAILABLE — {r.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
