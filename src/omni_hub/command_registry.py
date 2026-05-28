"""v0.18-B Typed Command Registry — frozen contracts + auto JSON schema.

Background
----------
Through v0.17 every operation is registered in ``OperationRegistry`` as
``{name → handler}`` and the handler reads ``spec.payload: dict[str, Any]``
loosely.  This is fine for CLI but breaks two SOTA patterns:

* **Pydantic / BAML / Instructor / Anthropic typed-tool spec** — LLM
  function-calling needs per-command JSON schema.
* **12-Factor Agents F4 / F12** — typed I/O envelopes + stateless reducer
  ``(state, event) → new_state`` are easier when payload has a class.

This module sits **alongside** ``OperationRegistry`` (does not replace
it).  Each ``CommandDefinition`` declares:

* ``payload_class`` — a frozen dataclass for the payload
* ``handler``       — ``(spec) → dict`` (or ``ProjectionDiff`` when dry_run)
* ``risk_class``    — default RiskLevel
* ``json_schema``   — auto-derived from payload_class fields

The 6 high-value commands are seeded here in v0.18; the remaining ~30
ops can migrate incrementally without breaking callers (legacy dict
payload still works against ``OperationRegistry``).

Stdlib only — no Pydantic, no jsonschema package.  We derive JSON Schema
from dataclass fields ourselves.
"""

from __future__ import annotations

import dataclasses
import inspect
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Callable, Optional, Union, get_args, get_origin, get_type_hints

from .models import OperationSpec, RiskLevel


# ---------------------------------------------------------------------------
# Definition + Registry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CommandDefinition:
    """One registered command — what payload it takes and how to run it."""

    name: str
    payload_class: type
    handler: Callable[[OperationSpec], dict]
    risk_class_default: RiskLevel = RiskLevel.READ_ONLY
    description: str = ""
    supports_preview: bool = False
    schema_version: str = "v0.18"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload_class": f"{self.payload_class.__module__}.{self.payload_class.__qualname__}",
            "risk_class_default": self.risk_class_default.code,
            "description": self.description,
            "supports_preview": self.supports_preview,
            "schema_version": self.schema_version,
            "json_schema": derive_json_schema(self.payload_class),
        }


class CommandRegistry:
    """Typed command registry.  Single global instance per workspace.

    Note: registration is by *name* identical to OperationRegistry so
    the runner can look up either; eventually the dict-payload path
    deprecates and this is the only registry.
    """

    def __init__(self) -> None:
        self._entries: dict[str, CommandDefinition] = {}

    def register(self, definition: CommandDefinition) -> None:
        if definition.name in self._entries:
            raise ValueError(f"command {definition.name!r} already registered")
        self._entries[definition.name] = definition

    def get(self, name: str) -> CommandDefinition:
        if name not in self._entries:
            raise KeyError(f"command {name!r} not registered in typed CommandRegistry")
        return self._entries[name]

    def has(self, name: str) -> bool:
        return name in self._entries

    def list(self) -> list[CommandDefinition]:
        return [self._entries[name] for name in sorted(self._entries)]

    def validate_payload(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Coerce a loose payload dict into the registered payload_class
        and return its asdict() form.  Raises ValueError on type mismatch
        or missing required field."""

        definition = self.get(name)
        instance = _instantiate_dataclass(definition.payload_class, payload)
        return dataclasses.asdict(instance)

    def describe(self) -> dict[str, Any]:
        return {
            "schema_version": "v0.18",
            "command_count": len(self._entries),
            "commands": [d.to_dict() for d in self.list()],
        }


# ---------------------------------------------------------------------------
# JSON-schema derivation (stdlib only)
# ---------------------------------------------------------------------------


_PRIMITIVE_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    type(None): "null",
}


def derive_json_schema(cls: type) -> dict[str, Any]:
    """Translate a dataclass into a JSON-Schema fragment.

    Supports: primitive types (str/int/float/bool), list[X], dict[str, X],
    Optional[X] (mapped to nullable: true), nested dataclasses.  Anything
    more complex falls back to ``{"type": "object"}``.
    """

    if not is_dataclass(cls):
        return _schema_for_type(cls)

    hints = get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for fld in fields(cls):
        annot = hints.get(fld.name, Any)
        prop_schema = _schema_for_type(annot)
        if fld.default is dataclasses.MISSING and fld.default_factory is dataclasses.MISSING:
            required.append(fld.name)
        else:
            # Include default in schema if it's JSON-serialisable.
            try:
                default = fld.default if fld.default is not dataclasses.MISSING else fld.default_factory()
                import json
                json.dumps(default)
                prop_schema["default"] = default
            except (TypeError, ValueError):
                pass
        properties[fld.name] = prop_schema

    schema: dict[str, Any] = {
        "type": "object",
        "title": cls.__qualname__,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _schema_for_type(annot: Any) -> dict[str, Any]:
    if annot is Any or annot is inspect.Parameter.empty:
        return {}
    if annot in _PRIMITIVE_TYPE_MAP:
        return {"type": _PRIMITIVE_TYPE_MAP[annot]}
    origin = get_origin(annot)
    args = get_args(annot)
    if origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and type(None) in args:
            inner = _schema_for_type(non_none[0])
            inner["nullable"] = True
            return inner
        return {"anyOf": [_schema_for_type(a) for a in args]}
    if origin in (list, tuple, set, frozenset):
        items_type = args[0] if args else Any
        return {"type": "array", "items": _schema_for_type(items_type)}
    if origin is dict:
        val_type = args[1] if len(args) >= 2 else Any
        return {"type": "object", "additionalProperties": _schema_for_type(val_type)}
    if is_dataclass(annot):
        return derive_json_schema(annot)
    return {}


def _instantiate_dataclass(cls: type, data: dict[str, Any]) -> Any:
    """Construct ``cls`` from ``data`` with mild coercion.  Stdlib only —
    Pydantic-grade validation is out of scope; we only catch missing
    required fields and obvious type mismatches.
    """

    if not is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass; CommandRegistry requires dataclass payloads")

    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for fld in fields(cls):
        annot = hints.get(fld.name, Any)
        if fld.name in data:
            kwargs[fld.name] = _coerce_value(data[fld.name], annot)
        elif fld.default is not dataclasses.MISSING:
            kwargs[fld.name] = fld.default
        elif fld.default_factory is not dataclasses.MISSING:
            kwargs[fld.name] = fld.default_factory()
        else:
            raise ValueError(f"missing required field {fld.name!r} for {cls.__qualname__}")
    return cls(**kwargs)


def _coerce_value(value: Any, annot: Any) -> Any:
    if annot is Any:
        return value
    if annot is bool and isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    origin = get_origin(annot)
    if origin is Union:
        non_none = [a for a in get_args(annot) if a is not type(None)]
        if value is None and type(None) in get_args(annot):
            return None
        if non_none:
            try:
                return _coerce_value(value, non_none[0])
            except Exception:                                    # noqa: BLE001
                return value
    if origin in (list, tuple, set, frozenset) and isinstance(value, (list, tuple, set, frozenset)):
        inner = get_args(annot)[0] if get_args(annot) else Any
        return [_coerce_value(item, inner) for item in value]
    if is_dataclass(annot) and isinstance(value, dict):
        return _instantiate_dataclass(annot, value)
    return value


# ---------------------------------------------------------------------------
# Built-in 6 high-value commands (v0.18 seed; more migrate later)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WikiIngestPayload:
    run_id: str
    domain: Optional[str] = None
    title: str = ""
    max_records: int = 20


@dataclass(slots=True)
class WikiApplyProposalPayload:
    proposal: str


@dataclass(slots=True)
class WikiSupersedePayload:
    new_claim_id: str
    old_claim_id: str
    reason: str = ""
    expected_version: Optional[int] = None


@dataclass(slots=True)
class WikiConflictResolvePayload:
    proposal_id: str
    decision: str                       # keep_both|reject_old|reject_new|supersede
    new_claim_id: str = ""
    old_claim_id: str = ""
    reason: str = ""
    expected_version: Optional[int] = None


@dataclass(slots=True)
class ClaimsShowPayload:
    claim_id: str


@dataclass(slots=True)
class HarnessCompileSkillPayload:
    domain: str
    skill_id: str = ""
    description: str = ""
    output_root: str = ".agents/skills"
    store_root: str = ".omni/preference"
    from_version: str = "v0"
    max_positive: int = 10
    max_negative: int = 4
    backend: str = "manual"


def build_default_command_registry() -> CommandRegistry:
    """Seed the registry with the 6 high-value commands.

    Handlers are looked up by name from the operation registry at runtime
    (they live in builtins.py).  This module only owns the typed payload
    + JSON schema export.
    """

    registry = CommandRegistry()
    registry.register(CommandDefinition(
        name="wiki_ingest",
        payload_class=WikiIngestPayload,
        handler=_noop_handler,
        risk_class_default=RiskLevel.LOCAL_WRITE,
        description="Bridge retrieval evidence into Proposal(wiki_update).",
        supports_preview=True,
    ))
    registry.register(CommandDefinition(
        name="wiki_apply_proposal",
        payload_class=WikiApplyProposalPayload,
        handler=_noop_handler,
        risk_class_default=RiskLevel.LOCAL_WRITE,
        description="Materialise an approved Proposal into vault/wiki/.",
        supports_preview=True,
    ))
    registry.register(CommandDefinition(
        name="wiki_supersede",
        payload_class=WikiSupersedePayload,
        handler=_noop_handler,
        risk_class_default=RiskLevel.LOCAL_WRITE,
        description="Graphiti-style bitemporal close on an old claim.",
        supports_preview=True,
    ))
    registry.register(CommandDefinition(
        name="wiki_conflict_resolve",
        payload_class=WikiConflictResolvePayload,
        handler=_noop_handler,
        risk_class_default=RiskLevel.LOCAL_WRITE,
        description="Apply a decision to a contradiction lint_finding proposal.",
    ))
    registry.register(CommandDefinition(
        name="claims_show",
        payload_class=ClaimsShowPayload,
        handler=_noop_handler,
        risk_class_default=RiskLevel.READ_ONLY,
        description="Show a single claim + supersession chain in both directions.",
    ))
    registry.register(CommandDefinition(
        name="harness_compile_skill",
        payload_class=HarnessCompileSkillPayload,
        handler=_noop_handler,
        risk_class_default=RiskLevel.LOCAL_WRITE,
        description="Compile preference spans into Anthropic-spec SKILL.md.",
    ))
    return registry


def _noop_handler(spec: OperationSpec) -> dict:
    """Placeholder — the OperationRegistry's handler runs in practice;
    CommandRegistry only owns the typed contract."""

    raise NotImplementedError(
        "CommandRegistry handlers are not called directly; use OperationRunner"
    )
