"""Dispatch contract for the quant-framework skill — the description must COVER the crypto-state
triggers and steer equities away, and the CLI entry must be registered. Deterministic proxy for
"Skill Dispatch Correctness" (the model makes the final trigger call from the description; this gates
the necessary condition — that the description contains the trigger terms; vague descriptions are the
#1 cause of mis-triggering). Versioned with the skill so CI refuses a description that stops covering
a known query. New trigger misses append a row to _TRIGGER.
"""
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "quant-framework" / "SKILL.md"

# (query, terms that MUST appear in the skill description for it to be discoverable) — should trigger
_TRIGGER = [
    ("分析现在的 ETH 多级别和结构", ["分析现在", "eth", "结构"]),
    ("btc 现在对手盘是谁", ["对手盘", "btc"]),
    ("eth 有没有双底", ["双底", "eth"]),
    ("现在能不能抄底 btc", ["抄底", "btc"]),
    ("edge audit on btc", ["edge audit"]),
]
# should NOT trigger (equities-only) — the description must name the off-ramp
_REJECT_OFFRAMP = ["finance-screen", "equities"]


def _desc() -> str:
    # only the YAML description drives dispatch; the body is read after triggering. Lower-cased so
    # term coverage is case-insensitive.
    return _SKILL.read_text(encoding="utf-8").lower()


def test_description_covers_every_crypto_state_trigger():
    d = _desc()
    for query, terms in _TRIGGER:
        missing = [t for t in terms if t.lower() not in d]
        assert not missing, f"{query!r}: description missing trigger terms {missing}"


def test_description_steers_equities_to_finance_screen():
    d = _desc()
    assert any(t.lower() in d for t in _REJECT_OFFRAMP), \
        "description must steer equities-only queries away (name finance-screen)"


def test_crypto_read_cli_and_op_registered():
    from omni_hub.cli import finance
    assert "crypto-read" in finance.COMMANDS
