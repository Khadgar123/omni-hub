"""Cross-skill knowledge transfer (v0.28).

Pipeline (deterministic, stdlib-only):

1. Read PreferenceStore for every domain under ``.omni/preference/``.
2. For each domain compute:
   * ``accepted_tokens`` — Counter of tokens in ``accepted_spans``
   * ``rejected_tokens`` — Counter of tokens in ``rejected_spans``
   * ``signal_strength``  — accepted_freq − rejected_freq, normalised
3. Identify **cross-skill signals**: a token that's a strong positive
   signal in ≥ 3 domains but absent / negative in others.  Those are
   the candidates for transfer.
4. For each candidate token + each under-utilising domain, emit a
   :class:`CrossSkillFinding` carrying the suggested SKILL.md update
   (an extra "preferred pattern" line in the body).

The output is *findings*, not direct writes.  Callers feed findings
into Proposal[T] (one Proposal per finding) and humans approve before
any SKILL.md is touched.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


# Token regex — keeps Latin word chars + CJK, drops punctuation.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}|[一-鿿]{2,}")


@dataclass(slots=True)
class PatternSignal:
    """One token's accepted/rejected signal across a single domain."""

    domain: str
    token: str
    accepted_count: int
    rejected_count: int

    @property
    def signal(self) -> float:
        """Normalised accepted − rejected, clamped to [-1.0, 1.0]."""

        total = self.accepted_count + self.rejected_count
        if total == 0:
            return 0.0
        return (self.accepted_count - self.rejected_count) / total

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["signal"] = round(self.signal, 4)
        return data


@dataclass(slots=True)
class CrossSkillFinding:
    """One actionable cross-skill transfer recommendation."""

    token: str
    strong_domains: list[str]       # where signal ≥ +threshold
    weak_domains: list[str]         # where signal ≤ −threshold OR absent
    suggested_update: str           # one-line SKILL.md addition for weak_domains
    sample_signal: float            # mean signal across strong_domains
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CrossSkillTransfer:
    """Driver: scan → detect → propose."""

    def __init__(
        self,
        workspace: Path | str = ".",
        *,
        preference_root: Path | str = ".omni/preference",
        min_strong_domains: int = 3,
        signal_threshold: float = 0.4,
        min_accepted_in_strong: int = 2,
        token_blocklist: Iterable[str] = (),
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.preference_root = self.workspace / Path(preference_root)
        self.min_strong_domains = max(2, int(min_strong_domains))
        self.signal_threshold = float(signal_threshold)
        self.min_accepted_in_strong = max(1, int(min_accepted_in_strong))
        self.token_blocklist = {t.lower() for t in token_blocklist} | _DEFAULT_BLOCKLIST

    # ---- public entry points -------------------------------------

    def scan(self) -> dict[str, dict[str, Counter[str]]]:
        """Returns ``{domain: {"accepted": Counter, "rejected": Counter}}``.

        Tokens are case-folded; tokens in the blocklist (common stop
        words + omni-hub jargon already on every SKILL.md) are dropped.
        """

        domain_tokens: dict[str, dict[str, Counter[str]]] = {}
        if not self.preference_root.exists():
            return domain_tokens
        for jsonl in self.preference_root.glob("*.jsonl"):
            domain = jsonl.stem
            accepted_counter: Counter[str] = Counter()
            rejected_counter: Counter[str] = Counter()
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for span in (record.get("accepted_spans") or []):
                    accepted_counter.update(self._tokens(str(span)))
                for span in (record.get("rejected_spans") or []):
                    rejected_counter.update(self._tokens(str(span)))
            domain_tokens[domain] = {
                "accepted": accepted_counter,
                "rejected": rejected_counter,
            }
        return domain_tokens

    def signals(self) -> list[PatternSignal]:
        """Flat list of per-domain per-token signal records."""

        out: list[PatternSignal] = []
        for domain, counters in self.scan().items():
            accepted = counters["accepted"]
            rejected = counters["rejected"]
            tokens = set(accepted) | set(rejected)
            for token in tokens:
                out.append(PatternSignal(
                    domain=domain, token=token,
                    accepted_count=accepted.get(token, 0),
                    rejected_count=rejected.get(token, 0),
                ))
        return out

    def find_transfers(self) -> list[CrossSkillFinding]:
        """Find tokens with strong positive signal in ≥
        ``min_strong_domains`` domains but absent/negative in others —
        each yields one ``CrossSkillFinding`` recommending an SKILL.md
        update for the under-utilising domains.
        """

        # Build a domain → {token: PatternSignal} index.
        per_domain: dict[str, dict[str, PatternSignal]] = {}
        for sig in self.signals():
            per_domain.setdefault(sig.domain, {})[sig.token] = sig

        all_domains = sorted(per_domain)
        all_tokens: set[str] = set()
        for tokens in per_domain.values():
            all_tokens.update(tokens)

        findings: list[CrossSkillFinding] = []
        for token in sorted(all_tokens):
            strong: list[tuple[str, float]] = []
            weak: list[str] = []
            for domain in all_domains:
                sig = per_domain[domain].get(token)
                if sig is None:
                    weak.append(domain)
                    continue
                if (sig.signal >= self.signal_threshold and
                        sig.accepted_count >= self.min_accepted_in_strong):
                    strong.append((domain, sig.signal))
                elif sig.signal <= -self.signal_threshold:
                    weak.append(domain)
                # else: neutral — neither strong nor weak; skip.

            if len(strong) < self.min_strong_domains or not weak:
                continue
            strong_domains = [d for d, _ in strong]
            mean_signal = sum(s for _, s in strong) / len(strong)
            findings.append(CrossSkillFinding(
                token=token,
                strong_domains=strong_domains,
                weak_domains=weak,
                suggested_update=self._format_suggestion(token, strong_domains, mean_signal),
                sample_signal=round(mean_signal, 4),
                metadata={
                    "min_strong_domains": self.min_strong_domains,
                    "signal_threshold": self.signal_threshold,
                },
            ))
        # Strongest signal first.
        findings.sort(key=lambda f: -f.sample_signal)
        return findings

    # ---- internals -----------------------------------------------

    def _tokens(self, text: str) -> list[str]:
        return [
            tok.lower() for tok in _TOKEN_RE.findall(text or "")
            if tok.lower() not in self.token_blocklist
        ]

    @staticmethod
    def _format_suggestion(
        token: str, strong_domains: list[str], mean_signal: float,
    ) -> str:
        return (
            f"Preferred pattern: include `{token}` "
            f"(cross-skill signal {mean_signal:+.2f} across "
            f"{', '.join(strong_domains)})."
        )


# Stop-word style blocklist — common English + Chinese fillers plus the
# omni-hub jargon already on every generated SKILL.md.
_DEFAULT_BLOCKLIST: set[str] = {
    "the", "and", "for", "with", "this", "that", "from", "into", "your",
    "you", "are", "was", "were", "have", "has", "had", "but", "not",
    "any", "all", "use", "uses", "using", "via", "per", "one", "two",
    "三", "四", "五", "六", "七", "八", "九", "十", "可以", "需要",
    "应该", "可能", "因此", "所以", "如果", "当然", "目前", "今天",
    "skill", "skills", "domain", "domains", "wiki", "agent", "proposal",
    "context", "vault", "claim", "claims", "omni", "hub",
}


__all__ = ["CrossSkillFinding", "CrossSkillTransfer", "PatternSignal"]
