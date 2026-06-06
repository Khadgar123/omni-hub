"""Contract evals for the quant-framework skill — grade the CONTRACT, not the answer ("the skill is
the contract", 2026 SOTA). Data-driven from research/evals/quant_framework.jsonl; each past miss
appends a case there (see research/error-ledger.md). Deterministic, so pass/fail IS the threshold and
the existing pytest run is the CI gate. The optional subjective LLM-judge layer is in
research/evals/README.md (omni-hub judge-evaluate — heuristic always available, never blocks CI).
"""
import json
from pathlib import Path

from quant import framework

_EVALS = Path(__file__).resolve().parent.parent / "research" / "evals" / "quant_framework.jsonl"


def _cases():
    return [json.loads(ln) for ln in _EVALS.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---- synthetic builders (structure scenarios; high/low straddle the close for strict pivots) ----
def _ramp(a, b, n):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _bars_from_path(path):
    prices = []
    for seg in path:
        prices += _ramp(seg[0], seg[1], int(seg[2]))[:-1]
    prices.append(path[-1][1])
    return [{"open": float(c), "high": float(c) * 1.002, "low": float(c) * 0.998, "close": float(c),
             "volume": 1000.0, "taker_buy": 500.0, "bucket_ts": (i + 1) * 14_400_000_000}
            for i, c in enumerate(prices)]


# ---- injected opener (output scenarios; no network) ----
class _Resp:
    def __init__(self, d): self._d = d
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _klines(n=260, start=100.0, rate=-0.002, taker_frac=0.45):
    rows, c = [], start
    for i in range(n):
        o = c; c = o * (1 + rate); h = max(o, c) * 1.002; lo = min(o, c) * 0.998
        rows.append([1700000000000 + i * 900000, f"{o}", f"{h}", f"{lo}", f"{c}", "1000.0",
                     0, "0", 10, f"{1000.0 * taker_frac}", "0", "0"])
    return rows


_ROUTES = [
    ("premiumIndex", {"markPrice": "70000", "indexPrice": "70010", "lastFundingRate": "0.0001"}),
    ("fundingRate", [{"fundingRate": "0.00003"} for _ in range(90)]),
    ("openInterest", {"openInterest": "100000"}),
    ("klines", _klines()),
]


def _opener(req, timeout=15.0):
    for sub, payload in _ROUTES:
        if sub in req.full_url:
            return _Resp(json.dumps(payload).encode("utf-8"))
    raise ValueError(req.full_url)


_READ = {}


def _read():
    if not _READ:
        _READ["r"] = framework.read("BTCUSDT", "binance", opener=_opener, with_macro=False,
                                    etf={"trend": "outflow", "note": "t"})
    return _READ["r"]


# ---- the gate ----
def test_golden_set_present_and_sized():
    assert 5 <= len(_cases()) <= 50               # the 5-8+ contract set; append one per miss


def test_structure_contract_cases():
    for c in (c for c in _cases() if c["kind"] == "structure"):
        sb = framework._structure_by_tf({c["tf"]: _bars_from_path(c["path"])})[c["tf"]]
        e = c["expect"]
        if "pattern_has" in e:
            assert sb["pattern"] and e["pattern_has"] in sb["pattern"], (c["id"], sb)
        if "pattern_excludes" in e:
            assert not (sb["pattern"] and e["pattern_excludes"] in sb["pattern"]), (c["id"], sb)
        if "base_low_lte" in e:
            assert sb["base_low"] <= e["base_low_lte"], (c["id"], sb)
        if "trend" in e:
            assert sb["trend"] == e["trend"], (c["id"], sb)


def test_output_contract_cases():
    r = _read()
    nar, rep = r["narrative"], r["report"]
    for c in (c for c in _cases() if c["kind"] == "output"):
        e = c["expect"]
        if "narrative_has" in e:
            assert e["narrative_has"] in nar, c["id"]
        if "report_has" in e:
            assert e["report_has"] in rep, c["id"]
        if "narrative_excludes" in e:
            for tok in e["narrative_excludes"]:
                assert tok not in nar, (c["id"], tok)
        if "report_order" in e:
            a, b = e["report_order"]
            assert rep.index(a) < rep.index(b), (c["id"], a, b)
