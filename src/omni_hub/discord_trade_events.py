"""Conservative, versioned trade-event extraction for verified Discord messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable, Sequence

from .discord_blogger_corpus import BloggerMessage


PROFILE_CHANNELS = {
    "coin-chief-v1": "1429001654799433838",
    "shuqin-v1": "1429001540529684540",
    "always-win-trader-v1": "1429003058154831905",
    "analyst-nick-v1": "1429001911545364581",
}

PROFILE_LABELS = {
    "coin-chief-v1": "币圈所长",
    "shuqin-v1": "舒琴",
    "always-win-trader-v1": "always-win-trader",
    "analyst-nick-v1": "分析师Nick",
}


@dataclass(frozen=True, slots=True)
class TradeEvent:
    event_id: str
    event_type: str
    profile: str
    message_id: str
    symbol: str | None
    direction: str | None
    effective_at: str
    entry: float | None
    entry_low: float | None
    entry_high: float | None
    tp: float | None
    tps: tuple[float, ...]
    sl: float | None
    evidence_ref: str

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "profile": self.profile,
            "message_id": self.message_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "effective_at": self.effective_at,
            "entry": self.entry,
            "entry_low": self.entry_low,
            "entry_high": self.entry_high,
            "tp": self.tp,
            "tps": list(self.tps),
            "sl": self.sl,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True, slots=True)
class MessageDecision:
    decision_id: str
    profile: str
    profile_version: str
    blogger: str
    message_id: str
    channel_id: str
    author_id: str | None
    snapshot_sha256: str
    effective_at: str
    classification: str
    exclusion_reason: str | None
    evidence_ref: str
    reply_message_id: str | None
    events: tuple[TradeEvent, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "profile": self.profile,
            "profile_version": self.profile_version,
            "blogger": self.blogger,
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "author_id": self.author_id,
            "snapshot_sha256": self.snapshot_sha256,
            "effective_at": self.effective_at,
            "classification": self.classification,
            "exclusion_reason": self.exclusion_reason,
            "evidence_ref": self.evidence_ref,
            "reply_message_id": self.reply_message_id,
            "events": [event.to_dict() for event in self.events],
        }


@dataclass(frozen=True, slots=True)
class TradeLifecycle:
    lifecycle_id: str
    profile: str
    blogger: str
    symbol: str
    direction: str
    opening_message_id: str
    linked_message_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    unresolved_event_ids: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "lifecycle_id": self.lifecycle_id,
            "profile": self.profile,
            "blogger": self.blogger,
            "symbol": self.symbol,
            "direction": self.direction,
            "opening_message_id": self.opening_message_id,
            "linked_message_ids": list(self.linked_message_ids),
            "event_ids": list(self.event_ids),
            "unresolved_event_ids": list(self.unresolved_event_ids),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class _ProfileRule:
    long_markers: tuple[str, ...]
    short_markers: tuple[str, ...]
    open_markers: tuple[str, ...]


_RULES = {
    "coin-chief-v1": _ProfileRule(("做多", "多单"), ("做空", "空单"), ("进场", "入场", "开仓")),
    "shuqin-v1": _ProfileRule(("多单", "做多"), ("空单", "做空"), ("入场", "进场", "开仓")),
    "always-win-trader-v1": _ProfileRule(("long", "多单", "做多"), ("short", "空单", "做空"), ("entry", "open", "入场")),
    "analyst-nick-v1": _ProfileRule(("long", "做多", "多单"), ("short", "做空", "空单"), ("entry", "open", "进场")),
}


_EVENT_MARKERS = (
    ("CANCEL", ("撤单", "取消", "cancel")),
    ("FILL", ("已成交", "成交", "filled", "fill")),
    ("PARTIAL_CLOSE", ("减仓", "部分止盈", "partial close", "partial")),
    ("AMEND", ("改止", "修改", "调整", "amend", "update")),
    ("TP", ("止盈", "take profit", "tp", "目标完成", "all targets")),
    ("SL", ("止损", "stop loss", "sl")),
    ("MANUAL_CLOSE", ("平仓", "清仓", "离场", "manual close", "close")),
)
_UNSUPPORTED_SYMBOLS = re.compile(r"\b(?:SOL|XRP|DOGE|BNB|ADA|AVAX|ETC|ZEC)(?:USDT)?\b", re.IGNORECASE)
PARSER_SCHEMA_VERSION = "discord-trade-parser-v2"
PARSER_IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_SYMBOL_ALIASES = {
    "BTCUSDT": ("BTC", "BTCUSDT", "比特币", "大饼"),
    "ETHUSDT": ("ETH", "ETHUSDT", "以太坊", "以太"),
}


def profile_config_descriptor(profile: str) -> dict[str, object]:
    try:
        rule = _RULES[profile]
    except KeyError as exc:
        raise ValueError("Discord blogger profile is unsupported") from exc
    return {
        "parser_schema_version": PARSER_SCHEMA_VERSION,
        "parser_implementation_sha256": PARSER_IMPLEMENTATION_SHA256,
        "profile": profile,
        "version": "v1",
        "channel_id": PROFILE_CHANNELS[profile],
        "blogger": PROFILE_LABELS[profile],
        "long_markers": rule.long_markers,
        "short_markers": rule.short_markers,
        "open_markers": rule.open_markers,
        "event_markers": _EVENT_MARKERS,
        "symbol_aliases": _SYMBOL_ALIASES,
        "unsupported_symbol_pattern": _UNSUPPORTED_SYMBOLS.pattern,
    }


def _profile_config_sha(profile: str) -> str:
    return hashlib.sha256(
        json.dumps(profile_config_descriptor(profile), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


PROFILE_CONFIG_SHA256 = {
    profile: _profile_config_sha(profile) for profile in _RULES
}


def parse_message(profile: str, message: BloggerMessage) -> MessageDecision:
    """Produce exactly one redacted decision from one verified message."""

    if profile not in _RULES:
        raise ValueError("Discord blogger profile is unsupported")
    effective_at = _normalized_time(message.edited_timestamp or message.timestamp)
    decision_id = _sha("decision", profile, message.message_id, message.snapshot_sha256, effective_at)
    base = {
        "decision_id": decision_id,
        "profile": profile,
        "profile_version": "v1",
        "blogger": PROFILE_LABELS[profile],
        "message_id": message.message_id,
        "channel_id": message.channel_id,
        "author_id": message.author_id,
        "snapshot_sha256": message.snapshot_sha256,
        "effective_at": effective_at,
        "evidence_ref": _safe_ref(message.snapshot_ref),
        "reply_message_id": message.reply_message_id,
    }
    if message.channel_id != PROFILE_CHANNELS[profile]:
        return MessageDecision(**base, classification="excluded", exclusion_reason="profile_channel_mismatch", events=())
    content = message.content.strip()
    if not content and message.media_occurrence_refs:
        return MessageDecision(**base, classification="excluded", exclusion_reason="media_only", events=())
    if _unsupported_symbol(content):
        return MessageDecision(**base, classification="excluded", exclusion_reason="unsupported_symbol", events=())
    symbol = _symbol(content)
    direction = _direction(content, _RULES[profile])
    event_types = _event_types(content, _RULES[profile])
    if not event_types:
        if symbol is not None or direction is not None:
            return MessageDecision(**base, classification="candidate", exclusion_reason="ambiguous_signal", events=())
        return MessageDecision(**base, classification="non_signal", exclusion_reason=None, events=())
    if (symbol is None or direction is None) and message.reply_message_id is None:
        return MessageDecision(**base, classification="candidate", exclusion_reason="ambiguous_signal", events=())
    entry_low, entry_high = _entry_range(content)
    entry = entry_low if entry_low == entry_high else None
    tps = _numbers_after(content, ("tp", "止盈", "take profit"))
    tp = tps[0] if tps else None
    sl = _number_after(content, ("sl", "止损", "stop loss", "改止损"))
    events = tuple(
        TradeEvent(
            event_id=_sha(
                "event-v1",
                decision_id,
                message.snapshot_sha256,
                {
                    "index": index,
                    "event_type": event_type,
                    "symbol": symbol,
                    "direction": direction,
                    "effective_at": effective_at,
                    "entry_low": entry_low,
                    "entry_high": entry_high,
                    "tps": tps,
                    "sl": sl,
                },
            ),
            event_type=event_type,
            profile=profile,
            message_id=message.message_id,
            symbol=symbol,
            direction=direction,
            effective_at=effective_at,
            entry=entry,
            entry_low=entry_low,
            entry_high=entry_high,
            tp=tp,
            tps=tps,
            sl=sl,
            evidence_ref=_safe_ref(message.snapshot_ref),
        )
        for index, event_type in enumerate(event_types)
    )
    return MessageDecision(**base, classification="event", exclusion_reason=None, events=events)


def link_trade_lifecycles(decisions: Sequence[MessageDecision]) -> tuple[TradeLifecycle, ...]:
    """Link only a unique reply or a unique active profile/symbol/direction match."""

    mutable: list[dict[str, object]] = []
    ordered = sorted(
        ((event, decision) for decision in decisions for event in decision.events),
        key=lambda pair: (pair[0].effective_at, int(pair[0].message_id), pair[0].event_id),
    )
    for event, decision in ordered:
        if event.event_type == "OPEN":
            assert event.symbol is not None and event.direction is not None
            mutable.append({
                "lifecycle_id": _sha("lifecycle-v1", event.profile, event.event_id),
                "profile": event.profile,
                "blogger": PROFILE_LABELS[event.profile],
                "symbol": event.symbol,
                "direction": event.direction,
                "opening_message_id": event.message_id,
                "linked_message_ids": [event.message_id],
                "event_ids": [event.event_id],
                "unresolved": [],
                "status": "open",
            })
            continue
        if decision.reply_message_id is not None:
            candidates = [
                item for item in mutable
                if item["profile"] == event.profile
                and decision.reply_message_id in item["linked_message_ids"]
            ]
        else:
            matching = [item for item in mutable if _matches(item, event)]
            candidates = [item for item in matching if item["status"] == "open"]
        if len(candidates) != 1:
            for item in candidates:
                item["unresolved"].append(event.event_id)  # type: ignore[index]
            continue
        target = candidates[0]
        if target["status"] != "open" or _reply_conflicts(target, event):
            target["unresolved"].append(event.event_id)  # type: ignore[index]
            continue
        target["event_ids"].append(event.event_id)  # type: ignore[index]
        if event.message_id not in target["linked_message_ids"]:
            target["linked_message_ids"].append(event.message_id)  # type: ignore[index]
        target["status"] = _next_status(event.event_type)
    return tuple(
        TradeLifecycle(
            lifecycle_id=str(item["lifecycle_id"]), profile=str(item["profile"]), blogger=str(item["blogger"]),
            symbol=str(item["symbol"]), direction=str(item["direction"]), opening_message_id=str(item["opening_message_id"]),
            linked_message_ids=tuple(item["linked_message_ids"]),
            event_ids=tuple(item["event_ids"]), unresolved_event_ids=tuple(item["unresolved"]), status=str(item["status"]),
        )
        for item in mutable
    )


def _matches(item: dict[str, object], event: TradeEvent) -> bool:
    return item["profile"] == event.profile and item["symbol"] == event.symbol and item["direction"] == event.direction


def _reply_conflicts(item: dict[str, object], event: TradeEvent) -> bool:
    return (
        (event.symbol is not None and event.symbol != item["symbol"])
        or (event.direction is not None and event.direction != item["direction"])
    )


def _next_status(event_type: str) -> str:
    return {
        "CANCEL": "cancelled",
        "TP": "closed_tp",
        "SL": "closed_sl",
        "MANUAL_CLOSE": "closed_manual",
    }.get(event_type, "open")


def _event_types(content: str, rule: _ProfileRule) -> tuple[str, ...]:
    normalized = content.casefold()
    if any(_has_marker(normalized, marker) and not _negated(normalized, marker) for marker in rule.open_markers):
        return ("OPEN",)
    amend_markers = next(markers for event_type, markers in _EVENT_MARKERS if event_type == "AMEND")
    if any(_has_marker(normalized, marker) and not _negated(normalized, marker) for marker in amend_markers):
        return ("AMEND",)
    found: list[str] = []
    for event_type, markers in _EVENT_MARKERS:
        if any(_has_marker(normalized, marker) and not _negated(normalized, marker) for marker in markers):
            found.append(event_type)
    return tuple(found)


def _symbol(content: str) -> str | None:
    upper = content.upper()
    if re.search(r"(?<![A-Z0-9])BTC(?:USDT)?(?![A-Z0-9])", upper):
        return "BTCUSDT"
    if re.search(r"(?<![A-Z0-9])ETH(?:USDT)?(?![A-Z0-9])", upper):
        return "ETHUSDT"
    if "比特币" in content or "大饼" in content:
        return "BTCUSDT"
    if "以太坊" in content or "以太" in content:
        return "ETHUSDT"
    return None


def _unsupported_symbol(content: str) -> bool:
    if _UNSUPPORTED_SYMBOLS.search(content):
        return True
    symbols = re.findall(r"\b([A-Z]{2,12})USDT\b", content.upper())
    return any(symbol not in {"BTC", "ETH"} for symbol in symbols)


def _direction(content: str, rule: _ProfileRule) -> str | None:
    normalized = content.casefold()
    long = any(_has_marker(normalized, marker) and not _negated(normalized, marker) for marker in rule.long_markers)
    short = any(_has_marker(normalized, marker) and not _negated(normalized, marker) for marker in rule.short_markers)
    if long == short:
        return None
    return "long" if long else "short"


def _number_after(content: str, markers: Iterable[str]) -> float | None:
    for marker in markers:
        match = re.search(rf"{re.escape(marker)}\s*[:：=]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", content, flags=re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def _entry_range(content: str) -> tuple[float | None, float | None]:
    for marker in ("entry", "进场", "入场", "开仓"):
        match = re.search(
            rf"{re.escape(marker)}\s*[:：=]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)(?:\s*(?:-|–|至|to)\s*([0-9][0-9,]*(?:\.[0-9]+)?))?",
            content,
            flags=re.IGNORECASE,
        )
        if match:
            first = float(match.group(1).replace(",", ""))
            second = float(match.group(2).replace(",", "")) if match.group(2) else first
            return min(first, second), max(first, second)
    return None, None


def _numbers_after(content: str, markers: Iterable[str]) -> tuple[float, ...]:
    for marker in markers:
        match = re.search(
            rf"{re.escape(marker)}\s*[:：=]?\s*([0-9][0-9,]*(?:\.[0-9]+)?(?:\s*[/,、]\s*[0-9][0-9,]*(?:\.[0-9]+)?)* )",
            content,
            flags=re.IGNORECASE | re.VERBOSE,
        )
        if match:
            values = tuple(float(value.replace(",", "")) for value in re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", match.group(1)))
            return values
    return ()


def _has_marker(content: str, marker: str) -> bool:
    if marker.isascii() and any(character.isalpha() for character in marker):
        return re.search(rf"(?<![a-z0-9]){re.escape(marker.casefold())}(?![a-z0-9])", content) is not None
    return marker.casefold() in content


def _negated(content: str, marker: str) -> bool:
    if marker.isascii() and any(character.isalpha() for character in marker):
        return re.search(rf"(?:not|no|un)\s*{re.escape(marker.casefold())}\b", content) is not None
    return any(prefix + marker.casefold() in content for prefix in ("未", "没有", "尚未", "不再", "不"))


def _normalized_time(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Discord message time must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


def _safe_ref(value: str) -> str:
    if not value or "http://" in value or "https://" in value or "logical_key" in value:
        raise ValueError("Discord evidence reference is unsafe")
    return value


def _sha(*values: object) -> str:
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
