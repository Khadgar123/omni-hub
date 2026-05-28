"""EvalCase + EvalPack + EvalStore (v0.41).

Per-domain (and per-functional-skill) versioned bench layer.  JSONL on
disk, manifest pinned, never overwritten — bump ``v0.X`` → ``v0.X+1``
to evolve (Iceberg-style atomic pointer pattern, scaled to single-user).

Schema is intentionally narrow: case_id + domain + eval_class +
question + expected + rubric_weights + graduated_from.  Anything richer
goes in ``metadata``.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


EVAL_ROOT = "vault/evals"
DEFAULT_VERSION = "v0.1"


# .gitignore guard: never include private holdout in the public repo.
HOLDOUT_FILENAME = "holdout-private.jsonl"
SEED_FILENAME = "seed.jsonl"
PACK_MANIFEST_FILENAME = "manifest.yaml"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _new_case_id() -> str:
    return f"eval_{secrets.token_hex(4)}"


def _normalise_version(version: str) -> str:
    """Accept "v0.1" or "0.1"; refuse anything else."""

    v = version.strip().lower()
    if not v:
        raise ValueError("version is required")
    if not v.startswith("v"):
        v = f"v{v}"
    if not re.fullmatch(r"v\d+\.\d+", v):
        raise ValueError(
            f"version {version!r} must look like 'v0.1' or '0.1'"
        )
    return v


class EvalClass(str, Enum):
    """Anthropic 2026-01 eval taxonomy."""

    CAPABILITY = "capability"     # low start pass-rate, room to improve
    REGRESSION = "regression"     # graduated capability, ~100% expected
    CALIBRATION = "calibration"   # rubric-based, two-expert agreement


@dataclass(slots=True)
class EvalCase:
    case_id: str
    domain: str                                 # "research" or "functional:pptx-build"
    eval_class: EvalClass
    question: str
    expected: str = ""                          # capability/regression target
    expected_traits: list[str] = field(default_factory=list)   # calibration rubric traits
    rubric_weights: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    graduated_from: str = ""                    # PreferenceRecord id (when graduated)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["eval_class"] = self.eval_class.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalCase":
        return cls(
            case_id=str(data["case_id"]),
            domain=str(data["domain"]),
            eval_class=EvalClass(str(data.get("eval_class", "capability"))),
            question=str(data["question"]),
            expected=str(data.get("expected", "")),
            expected_traits=list(data.get("expected_traits", [])),
            rubric_weights={
                str(k): float(v) for k, v in (data.get("rubric_weights") or {}).items()
            },
            metadata=dict(data.get("metadata", {})),
            graduated_from=str(data.get("graduated_from", "")),
            created_at=str(data.get("created_at") or _utcnow()),
        )


@dataclass(slots=True)
class EvalPack:
    """One versioned bench pack."""

    pack_id: str                                # e.g. "research/v0.1"
    domain: str
    version: str                                # "v0.1"
    eval_class_counts: dict[str, int] = field(default_factory=dict)
    source: str = "hand-curated"
    rubric_ref: str = ""                        # path or "domain_profiles.<slug>"
    seed_path: str = ""                         # relative to workspace
    holdout_path: str = ""                      # relative; gitignored
    notes_path: str = ""
    superseded_by: str = ""                     # next pack_id when graduated
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvalStore:
    """Filesystem-backed eval pack registry.

    Layout:
        vault/evals/<domain>/<version>/seed.jsonl              # public
        vault/evals/<domain>/<version>/manifest.yaml           # EvalPack
        vault/evals/<domain>/<version>/holdout-private.jsonl   # gitignored
        vault/evals/<domain>/<version>/notes.md                # human notes
        vault/evals/manifest.json                              # all packs index
    """

    def __init__(self, workspace: Path | str = ".") -> None:
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / EVAL_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "manifest.json"

    # ---- pack lifecycle -----------------------------------------

    def create_pack(
        self,
        *,
        domain: str,
        version: str = DEFAULT_VERSION,
        source: str = "hand-curated",
        rubric_ref: str = "",
        notes: str = "",
    ) -> EvalPack:
        version = _normalise_version(version)
        pack_id = f"{domain}/{version}"
        pack_dir = self.root / domain / version
        if pack_dir.exists() and (pack_dir / SEED_FILENAME).exists():
            raise ValueError(
                f"pack {pack_id} already exists; bump version (v0.X+1) "
                f"instead of overwriting (HR #11)"
            )
        pack_dir.mkdir(parents=True, exist_ok=True)
        pack = EvalPack(
            pack_id=pack_id,
            domain=domain,
            version=version,
            source=source,
            rubric_ref=rubric_ref or f"domain_profiles.{domain}",
            seed_path=str((pack_dir / SEED_FILENAME).relative_to(self.workspace)),
            holdout_path=str(
                (pack_dir / HOLDOUT_FILENAME).relative_to(self.workspace),
            ),
            notes_path=str((pack_dir / "notes.md").relative_to(self.workspace))
            if notes else "",
        )
        # Touch empty seed file so list_cases works immediately.
        (pack_dir / SEED_FILENAME).touch(exist_ok=True)
        self._write_manifest(pack, notes=notes)
        self._update_index(pack)
        return pack

    def get_pack(self, domain: str, version: str = DEFAULT_VERSION) -> EvalPack | None:
        version = _normalise_version(version)
        manifest_path = self.root / domain / version / PACK_MANIFEST_FILENAME
        if not manifest_path.exists():
            return None
        return self._read_manifest(manifest_path)

    def list_packs(self) -> list[EvalPack]:
        out: list[EvalPack] = []
        for domain_dir in sorted(self.root.iterdir()):
            if not domain_dir.is_dir():
                continue
            for version_dir in sorted(domain_dir.iterdir()):
                manifest = version_dir / PACK_MANIFEST_FILENAME
                if manifest.exists():
                    out.append(self._read_manifest(manifest))
        return out

    def supersede_pack(self, old_pack_id: str, new_pack_id: str) -> None:
        """Mark v0.X as superseded by v0.X+1 (bitemporal close)."""

        old_domain, old_version = old_pack_id.split("/", 1)
        manifest_path = self.root / old_domain / old_version / PACK_MANIFEST_FILENAME
        pack = self._read_manifest(manifest_path)
        pack.superseded_by = new_pack_id
        self._write_manifest(pack)
        self._update_index(pack)

    # ---- case CRUD ----------------------------------------------

    def add_case(self, pack: EvalPack, case: EvalCase, *, holdout: bool = False) -> Path:
        if case.domain != pack.domain:
            raise ValueError(
                f"case.domain={case.domain!r} mismatches pack.domain={pack.domain!r}"
            )
        target_file = (
            self.workspace / pack.holdout_path if holdout else self.workspace / pack.seed_path
        )
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
        # Update class counts (public only).
        if not holdout:
            counts = pack.eval_class_counts or {}
            counts[case.eval_class.value] = counts.get(case.eval_class.value, 0) + 1
            pack.eval_class_counts = counts
            self._write_manifest(pack)
            self._update_index(pack)
        return target_file

    def list_cases(
        self,
        pack: EvalPack,
        *,
        include_holdout: bool = False,
        eval_class: EvalClass | None = None,
    ) -> list[EvalCase]:
        out: list[EvalCase] = []
        seed_path = self.workspace / pack.seed_path
        if seed_path.exists():
            out.extend(self._read_jsonl(seed_path))
        if include_holdout:
            holdout_path = self.workspace / pack.holdout_path
            if holdout_path.exists():
                out.extend(self._read_jsonl(holdout_path))
        if eval_class:
            out = [c for c in out if c.eval_class is eval_class]
        return out

    # ---- internals ----------------------------------------------

    def _read_jsonl(self, path: Path) -> list[EvalCase]:
        cases: list[EvalCase] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(EvalCase.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError):
                continue
        return cases

    def _write_manifest(self, pack: EvalPack, *, notes: str = "") -> None:
        manifest_path = (
            self.workspace / pack.seed_path
        ).parent / PACK_MANIFEST_FILENAME
        # Minimal YAML-compatible JSON-shaped manifest (stdlib only).
        body = (
            f"pack_id: {pack.pack_id}\n"
            f"domain: {pack.domain}\n"
            f"version: {pack.version}\n"
            f"source: {pack.source}\n"
            f"rubric_ref: {pack.rubric_ref}\n"
            f"seed_path: {pack.seed_path}\n"
            f"holdout_path: {pack.holdout_path}\n"
            f"superseded_by: {pack.superseded_by}\n"
            f"created_at: {pack.created_at}\n"
            f"eval_class_counts:\n"
            + "\n".join(
                f"  {k}: {v}" for k, v in (pack.eval_class_counts or {}).items()
            )
            + "\n"
        )
        manifest_path.write_text(body, encoding="utf-8")
        if notes:
            notes_path = manifest_path.parent / "notes.md"
            notes_path.write_text(notes, encoding="utf-8")

    def _read_manifest(self, path: Path) -> EvalPack:
        # Tiny stdlib-only YAML reader — same subset we use elsewhere.
        text = path.read_text(encoding="utf-8")
        fields: dict[str, Any] = {"eval_class_counts": {}}
        in_counts = False
        for raw in text.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if in_counts and raw.startswith("  "):
                k, _, v = raw.strip().partition(":")
                if k and v:
                    fields["eval_class_counts"][k.strip()] = int(v.strip())
                continue
            in_counts = False
            if raw.rstrip().endswith(":") and not raw.startswith(" "):
                in_counts = raw.startswith("eval_class_counts")
                continue
            key, _, val = raw.partition(":")
            fields[key.strip()] = val.strip()
        return EvalPack(
            pack_id=str(fields.get("pack_id", "")),
            domain=str(fields.get("domain", "")),
            version=str(fields.get("version", "")),
            eval_class_counts=fields.get("eval_class_counts", {}),
            source=str(fields.get("source", "")),
            rubric_ref=str(fields.get("rubric_ref", "")),
            seed_path=str(fields.get("seed_path", "")),
            holdout_path=str(fields.get("holdout_path", "")),
            superseded_by=str(fields.get("superseded_by", "")),
            created_at=str(fields.get("created_at", "")),
        )

    def _update_index(self, pack: EvalPack) -> None:
        index: dict[str, Any] = {}
        if self._index_path.exists():
            try:
                index = json.loads(self._index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                index = {}
        index[pack.pack_id] = pack.to_dict()
        self._index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8",
        )


__all__ = [
    "DEFAULT_VERSION",
    "EVAL_ROOT",
    "EvalCase",
    "EvalClass",
    "EvalPack",
    "EvalStore",
    "HOLDOUT_FILENAME",
    "SEED_FILENAME",
]
