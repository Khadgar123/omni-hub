from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import unittest

from omni_hub.discord_blogger_corpus import BloggerMessage
from omni_hub.discord_trade_events import (
    PARSER_IMPLEMENTATION_SHA256,
    PROFILE_CONFIG_SHA256,
    profile_config_descriptor,
    PROFILE_CHANNELS,
    link_trade_lifecycles,
    parse_message,
)


def _message(
    message_id: str,
    channel_id: str,
    content: str,
    *,
    timestamp: str = "2026-07-20T10:00:00+00:00",
    edited: str | None = None,
    reply: str | None = None,
    media: tuple[str, ...] = (),
) -> BloggerMessage:
    return BloggerMessage(
        message_id=message_id,
        channel_id=channel_id,
        author_id="999",
        timestamp=timestamp,
        edited_timestamp=edited,
        content=content,
        reply_message_id=reply,
        snapshot_ref=f"evidence/messages.json#/{message_id}",
        snapshot_sha256="a" * 64,
        media_occurrence_refs=media,
    )


class TradeEventParsingTests(unittest.TestCase):
    def test_profile_commitments_include_the_parser_implementation_digest(self) -> None:
        self.assertRegex(PARSER_IMPLEMENTATION_SHA256, r"^[0-9a-f]{64}$")
        for profile, digest in PROFILE_CONFIG_SHA256.items():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            descriptor = profile_config_descriptor(profile)
            self.assertEqual(descriptor["parser_implementation_sha256"], PARSER_IMPLEMENTATION_SHA256)
            self.assertEqual(
                digest,
                hashlib.sha256(json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            )

    def test_versioned_profile_channels_are_pinned(self) -> None:
        self.assertEqual(
            PROFILE_CHANNELS,
            {
                "coin-chief-v1": "1429001654799433838",
                "shuqin-v1": "1429001540529684540",
                "always-win-trader-v1": "1429003058154831905",
                "analyst-nick-v1": "1429001911545364581",
            },
        )

    def test_each_blogger_golden_open_has_one_decision_and_no_content(self) -> None:
        cases = (
            ("coin-chief-v1", "BTC 做多 进场 100000，止盈 101000，止损 99000"),
            ("shuqin-v1", "ETH多单，入场 3000，TP 3150，SL 2950"),
            ("always-win-trader-v1", "long BTCUSDT entry 100000 tp 101000 sl 99000"),
            ("analyst-nick-v1", "ETHUSDT short entry 3000 TP 2900 SL 3050"),
        )
        for index, (profile, content) in enumerate(cases, start=1):
            with self.subTest(profile=profile):
                decision = parse_message(
                    profile,
                    _message(str(index), PROFILE_CHANNELS[profile], content),
                )
                self.assertEqual(decision.classification, "event")
                self.assertEqual([event.event_type for event in decision.events], ["OPEN"])
                self.assertEqual(decision.events[0].symbol, "BTCUSDT" if "BTC" in content else "ETHUSDT")
                self.assertNotIn(content, repr(decision.to_dict()))
                self.assertEqual(decision.evidence_ref, f"evidence/messages.json#/{index}")

    def test_parses_all_supported_event_kinds_and_stable_sha_ids(self) -> None:
        profile = "coin-chief-v1"
        channel = PROFILE_CHANNELS[profile]
        examples = {
            "BTC 做多 进场 100000": "OPEN",
            "BTC 多单改止损 99500": "AMEND",
            "BTC 多单撤单": "CANCEL",
            "BTC 多单已成交": "FILL",
            "BTC 多单减仓一半": "PARTIAL_CLOSE",
            "BTC 多单止盈": "TP",
            "BTC 多单止损": "SL",
            "BTC 多单平仓": "MANUAL_CLOSE",
        }
        for index, (content, expected) in enumerate(examples.items(), start=10):
            with self.subTest(expected=expected):
                message = _message(str(index), channel, content)
                first = parse_message(profile, message)
                second = parse_message(profile, message)
                self.assertEqual([event.event_type for event in first.events], [expected])
                self.assertEqual(first.to_dict(), second.to_dict())
                self.assertEqual(len(first.events[0].event_id), 64)

    def test_edited_timestamp_is_effective_time_and_media_or_unsupported_are_excluded(self) -> None:
        profile = "shuqin-v1"
        channel = PROFILE_CHANNELS[profile]
        edited = parse_message(
            profile,
            _message("30", channel, "BTC 做多 入场 100000", edited="2026-07-20T11:00:00+00:00"),
        )
        self.assertEqual(edited.effective_at, "2026-07-20T11:00:00+00:00")
        offset = parse_message(
            profile,
            _message("34", channel, "BTC 做多 入场 100000", edited="2026-07-20T11:00:00+08:00"),
        )
        self.assertEqual(offset.effective_at, "2026-07-20T03:00:00+00:00")
        unsupported = parse_message(profile, _message("31", channel, "SOL 做多 入场 100"))
        etc = parse_message(profile, _message("33", channel, "ETC 做多 入场 20"))
        media_only = parse_message(profile, _message("32", channel, "", media=("evidence#media",)))
        self.assertEqual((unsupported.classification, unsupported.exclusion_reason), ("excluded", "unsupported_symbol"))
        self.assertEqual((etc.classification, etc.exclusion_reason), ("excluded", "unsupported_symbol"))
        self.assertEqual((media_only.classification, media_only.exclusion_reason), ("excluded", "media_only"))

    def test_non_unique_lifecycle_update_stays_unresolved(self) -> None:
        profile = "always-win-trader-v1"
        channel = PROFILE_CHANNELS[profile]
        first = parse_message(profile, _message("40", channel, "BTC long entry 100000"))
        second = parse_message(profile, _message("41", channel, "BTC long entry 101000", timestamp="2026-07-20T10:01:00+00:00"))
        update = parse_message(profile, _message("42", channel, "BTC long 止盈", timestamp="2026-07-20T10:02:00+00:00"))
        lifecycles = link_trade_lifecycles((first, second, update))
        self.assertEqual(len(lifecycles), 2)
        self.assertEqual(lifecycles[0].unresolved_event_ids, (update.events[0].event_id,))
        self.assertEqual(lifecycles[1].unresolved_event_ids, (update.events[0].event_id,))

    def test_reply_uniquely_links_and_closes_a_lifecycle(self) -> None:
        profile = "analyst-nick-v1"
        channel = PROFILE_CHANNELS[profile]
        open_decision = parse_message(profile, _message("50", channel, "ETH short entry 3000"))
        close_decision = parse_message(profile, _message("51", channel, "ETH short 止盈", reply="50"))
        lifecycle = link_trade_lifecycles((open_decision, close_decision))[0]
        self.assertEqual(lifecycle.status, "closed_tp")
        self.assertEqual(lifecycle.event_ids, (open_decision.events[0].event_id, close_decision.events[0].event_id))
        self.assertEqual(len(lifecycle.lifecycle_id), 64)

    def test_preserves_entry_range_and_every_target_without_raw_content(self) -> None:
        profile = "analyst-nick-v1"
        decision = parse_message(
            profile,
            _message(
                "60", PROFILE_CHANNELS[profile],
                "BTC多单 entry 63200-60800 TP 65600/67100 SL 60000",
            ),
        )
        event = decision.events[0]
        self.assertEqual((event.entry_low, event.entry_high), (60800.0, 63200.0))
        self.assertEqual(event.tps, (65600.0, 67100.0))
        self.assertNotIn("63200-60800", repr(event.to_dict()))

    def test_conflicting_close_remains_explicitly_unresolved(self) -> None:
        profile = "always-win-trader-v1"
        channel = PROFILE_CHANNELS[profile]
        opening = parse_message(profile, _message("61", channel, "ETH long entry 1852 TP 1880 SL 1825"))
        cancelled = parse_message(profile, _message("62", channel, "ETH long cancel", reply="61"))
        completed = parse_message(profile, _message("63", channel, "ETH long 所有目标完成", reply="61"))
        lifecycle = link_trade_lifecycles((opening, cancelled, completed))[0]
        self.assertEqual(lifecycle.status, "cancelled")
        self.assertEqual(lifecycle.unresolved_event_ids, (completed.events[0].event_id,))

    def test_reply_only_update_inherits_a_unique_opening_but_non_reply_stays_ambiguous(self) -> None:
        profile = "coin-chief-v1"
        channel = PROFILE_CHANNELS[profile]
        opening = parse_message(profile, _message("64", channel, "BTC 做多 入场 100000"))
        reply = parse_message(profile, _message("65", channel, "止盈", reply="64"))
        standalone = parse_message(profile, _message("66", channel, "止盈"))
        lifecycle = link_trade_lifecycles((opening, reply))[0]
        self.assertEqual(reply.events[0].event_type, "TP")
        self.assertIsNone(reply.events[0].symbol)
        self.assertEqual(lifecycle.status, "closed_tp")
        self.assertEqual((standalone.classification, standalone.exclusion_reason), ("candidate", "ambiguous_signal"))

    def test_negated_action_words_do_not_create_fill_or_cancel(self) -> None:
        profile = "coin-chief-v1"
        channel = PROFILE_CHANNELS[profile]
        for index, content in enumerate(("BTC 多单未成交", "BTC 多单没有成交", "BTC 不再做空"), start=67):
            with self.subTest(content=content):
                decision = parse_message(profile, _message(str(index), channel, content))
                self.assertNotIn("FILL", [event.event_type for event in decision.events])
                self.assertNotIn("CANCEL", [event.event_type for event in decision.events])

    def test_chinese_symbol_aliases_work_but_plain_direction_words_do_not(self) -> None:
        profile = "shuqin-v1"
        channel = PROFILE_CHANNELS[profile]
        btc = parse_message(profile, _message("70", channel, "大饼 做多 入场 100000"))
        eth = parse_message(profile, _message("71", channel, "以太 做空 入场 3000"))
        ordinary = parse_message(profile, _message("72", channel, "今天多喝水，天气不错"))
        self.assertEqual(btc.events[0].symbol, "BTCUSDT")
        self.assertEqual(eth.events[0].symbol, "ETHUSDT")
        self.assertEqual(ordinary.classification, "non_signal")

    def test_english_markers_require_word_boundaries(self) -> None:
        profile = "always-win-trader-v1"
        decision = parse_message(
            profile,
            _message("73", PROFILE_CHANNELS[profile], "BTC long-form note: https://example.invalid/topic"),
        )
        self.assertEqual(decision.classification, "candidate")
        self.assertEqual(decision.events, ())

    def test_negated_open_and_amend_markers_never_create_executable_events(self) -> None:
        profile = "coin-chief-v1"
        channel = PROFILE_CHANNELS[profile]
        for index, content in enumerate(("BTC 做多 未入场", "BTC 做多 不再开仓", "BTC 多单 不修改", "BTC 多单 尚未调整", "BTC 不做多入场", "BTC 不做空开仓"), start=80):
            with self.subTest(content=content):
                decision = parse_message(profile, _message(str(index), channel, content))
                self.assertEqual(decision.events, ())
                self.assertEqual(decision.classification, "candidate")

    def test_reply_inherits_only_missing_fields_and_accepts_any_linked_event_message(self) -> None:
        profile = "always-win-trader-v1"
        channel = PROFILE_CHANNELS[profile]
        opening = parse_message(profile, _message("90", channel, "BTC long entry 100000"))
        filled = parse_message(profile, _message("91", channel, "BTC long filled", reply="90"))
        reply_to_fill = parse_message(profile, _message("92", channel, "止盈", reply="91"))
        mismatch = parse_message(profile, _message("93", channel, "ETH short 止盈", reply="90"))
        lifecycle = link_trade_lifecycles((opening, filled, reply_to_fill, mismatch))[0]
        self.assertEqual(lifecycle.status, "closed_tp")
        self.assertIn(reply_to_fill.events[0].event_id, lifecycle.event_ids)
        self.assertIn(mismatch.events[0].event_id, lifecycle.unresolved_event_ids)
        self.assertEqual(lifecycle.symbol, "BTCUSDT")

    def test_reply_with_explicit_conflicting_fields_leaves_the_target_open(self) -> None:
        profile = "analyst-nick-v1"
        channel = PROFILE_CHANNELS[profile]
        opening = parse_message(profile, _message("94", channel, "BTC long entry 100000"))
        conflict = parse_message(profile, _message("96", channel, "ETH short 止盈", reply="94"))
        lifecycle = link_trade_lifecycles((opening, conflict))[0]
        self.assertEqual(lifecycle.status, "open")
        self.assertEqual(lifecycle.event_ids, (opening.events[0].event_id,))
        self.assertEqual(lifecycle.unresolved_event_ids, (conflict.events[0].event_id,))

    def test_event_and_lifecycle_ids_change_with_snapshot_or_semantic_payload(self) -> None:
        profile = "analyst-nick-v1"
        channel = PROFILE_CHANNELS[profile]
        first = parse_message(profile, _message("95", channel, "BTC long entry 100000",))
        changed_price = parse_message(profile, _message("95", channel, "BTC long entry 101000",))
        changed_snapshot = parse_message(profile, BloggerMessage(
            message_id="95", channel_id=channel, author_id="999", timestamp="2026-07-20T10:00:00+00:00",
            edited_timestamp=None, content="BTC long entry 100000", reply_message_id=None,
            snapshot_ref="evidence/messages.json#/95", snapshot_sha256="c" * 64, media_occurrence_refs=(),
        ))
        self.assertNotEqual(first.events[0].event_id, changed_price.events[0].event_id)
        self.assertNotEqual(first.events[0].event_id, changed_snapshot.events[0].event_id)
        self.assertNotEqual(
            link_trade_lifecycles((first,))[0].lifecycle_id,
            link_trade_lifecycles((changed_snapshot,))[0].lifecycle_id,
        )


if __name__ == "__main__":
    unittest.main()
