"""v0.31-v0.36 P1 tests: Application Plane expansion (8 functions)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.channels.base import InboundMessage
from omni_hub.finance_ops import (
    AlertRule,
    FinanceAnalyst,
    OrderIntent,
    OrderSide,
    OrderType,
    PortfolioSnapshot,
    risk_check,
    HARD_BLOCK_POSITION_FRACTION,
    WARN_POSITION_FRACTION,
)
from omni_hub.inbox import ForwardedContentRouter, InboxCategory
from omni_hub.pptx import (
    Bullet,
    DeckOutline,
    Slide,
    StubPPTXBuilder,
)
from omni_hub.projects import (
    Project,
    ProjectStatus,
    ProjectStore,
    SubTask,
)
from omni_hub.scheduling import (
    CalendarEvent,
    CalendarStore,
    EventKind,
    EventStatus,
    PersonalTask,
    PersonalTaskStore,
    PlannedBlock,
    TaskCategory,
    TaskStatus,
    TimeBlockPlanner,
)
from omni_hub.users import (
    ArchivalEntry,
    DEFAULT_USER_HANDLE,
    MemoryTier,
    PerUserMemoryStore,
    RecallEntry,
    UserProfile,
    UserProfileStore,
    UserStatus,
)


# ---------------------------------------------------------------------------
# v0.31 — Users
# ---------------------------------------------------------------------------


class UserProfileStoreTests(unittest.TestCase):
    def test_default_user_created_on_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserProfileStore(tmp)
            default = store.get_by_handle(DEFAULT_USER_HANDLE)
            self.assertIsNotNone(default)
            self.assertEqual(default.status, UserStatus.ACTIVE)

    def test_enroll_then_approve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserProfileStore(tmp)
            new_user = store.enroll(handle="alice")
            self.assertEqual(new_user.status, UserStatus.PENDING)
            approved = store.approve(new_user.user_id)
            self.assertEqual(approved.status, UserStatus.ACTIVE)

    def test_resolve_default_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserProfileStore(tmp)
            resolved = store.resolve("")
            self.assertEqual(resolved.handle, DEFAULT_USER_HANDLE)

    def test_persona_block_size_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserProfileStore(tmp)
            default = store.get_by_handle(DEFAULT_USER_HANDLE)
            with self.assertRaises(ValueError):
                store.set_persona_block(default.user_id, "x" * 5000)

    def test_set_style_prefs_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserProfileStore(tmp)
            default = store.get_by_handle(DEFAULT_USER_HANDLE)
            updated = store.set_style_prefs(
                default.user_id, {"tone": "formal", "language": "en"},
            )
            self.assertEqual(updated.style_prefs["tone"], "formal")


class PerUserMemoryTests(unittest.TestCase):
    def test_recall_append_then_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem = PerUserMemoryStore(tmp)
            mem.append_recall(RecallEntry(
                user_id="u_test", summary="had coffee with X about ACE paper",
                skill_id="research",
            ))
            entries = mem.list_recall("u_test")
            self.assertEqual(len(entries), 1)
            self.assertIn("ACE", entries[0].summary)

    def test_archival_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem = PerUserMemoryStore(tmp)
            mem.append_archival(ArchivalEntry(
                user_id="u_test", role="user",
                body="What about Mem0's bitemporal validity?",
            ))
            mem.append_archival(ArchivalEntry(
                user_id="u_test", role="agent",
                body="Mem0 uses Graphiti-style supersedes/superseded_by.",
            ))
            hits = mem.search_archival("u_test", "bitemporal")
            self.assertEqual(len(hits), 1)
            self.assertIn("bitemporal", hits[0].body.lower())

    def test_overview_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem = PerUserMemoryStore(tmp)
            mem.append_recall(RecallEntry(user_id="u_x", summary="s"))
            mem.append_archival(ArchivalEntry(user_id="u_x", role="user", body="b"))
            ov = mem.overview("u_x")
            self.assertEqual(ov["recall_entries"], 1)
            self.assertEqual(ov["archival_entries"], 1)


# ---------------------------------------------------------------------------
# v0.32 — Scheduling
# ---------------------------------------------------------------------------


class CalendarStoreTests(unittest.TestCase):
    def test_add_event_then_list_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cal = CalendarStore(tmp)
            start = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
            end = start + timedelta(hours=1)
            event = cal.add_event(
                user_id="u_a", summary="standup",
                start=start, end=end,
                categories=["work"],
            )
            events = cal.list_events("u_a")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].summary, "standup")
            self.assertEqual(events[0].duration_minutes(), 60)

    def test_ical_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cal = CalendarStore(tmp)
            start = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
            cal.add_event(
                user_id="u_a", summary="standup",
                start=start, end=start + timedelta(hours=1),
            )
            ics = cal.export_ics("u_a")
            self.assertIn("BEGIN:VCALENDAR", ics)
            self.assertIn("BEGIN:VEVENT", ics)
            self.assertIn("SUMMARY:standup", ics)

    def test_import_ics_adds_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cal = CalendarStore(tmp)
            ics_body = (
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
                "BEGIN:VEVENT\r\nUID:test-1@example\r\n"
                "DTSTART:20260601T140000Z\r\n"
                "DTEND:20260601T150000Z\r\n"
                "SUMMARY:Imported event\r\n"
                "STATUS:CONFIRMED\r\n"
                "DTSTAMP:20260530T000000Z\r\n"
                "LAST-MODIFIED:20260530T000000Z\r\n"
                "END:VEVENT\r\nEND:VCALENDAR\r\n"
            )
            imported = cal.import_ics("u_b", ics_body)
            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0].summary, "Imported event")


class PersonalTaskStoreTests(unittest.TestCase):
    def test_add_then_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = PersonalTaskStore(tmp)
            tasks.add(
                user_id="u_a", title="watch ACE paper",
                category=TaskCategory.RESEARCH,
                priority=2, estimated_minutes=90,
            )
            tasks.add(
                user_id="u_a", title="reply to email",
                category=TaskCategory.WORK,
                priority=4, estimated_minutes=15,
            )
            listed = tasks.list(user_id="u_a")
            self.assertEqual(len(listed), 2)
            self.assertEqual(listed[0].priority, 2)   # highest priority first

    def test_status_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = PersonalTaskStore(tmp)
            t = tasks.add(user_id="u_a", title="x")
            updated = tasks.update_status(t.task_id, TaskStatus.DONE)
            self.assertEqual(updated.status, TaskStatus.DONE)
            self.assertTrue(updated.completed_at)

    def test_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = PersonalTaskStore(tmp)
            for i in range(3):
                tasks.add(user_id="u_a", title=f"t{i}")
            for i in range(2):
                tasks.add(user_id="u_b", title=f"b{i}")
            stats = tasks.stats(user_id="u_a")
            self.assertEqual(stats["total"], 3)
            self.assertEqual(stats["tally"]["open"], 3)


class TimeBlockPlannerTests(unittest.TestCase):
    def test_plan_places_tasks_into_free_slots(self) -> None:
        # Window: 2026-06-01 (Monday), 09-12 free
        window_start = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        tasks = [
            PersonalTask(
                task_id="t1", user_id="u_a", title="A",
                priority=1, estimated_minutes=30,
                category=TaskCategory.WORK,
            ),
            PersonalTask(
                task_id="t2", user_id="u_a", title="B",
                priority=2, estimated_minutes=60,
                category=TaskCategory.WORK,
            ),
        ]
        planner = TimeBlockPlanner()
        blocks = planner.plan(tasks, events=[], window_start=window_start, window_end=window_end)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].task_id, "t1")
        # First slot starts at 09:00 (work_start) for t1.
        self.assertEqual(blocks[0].start.hour, 9)

    def test_unfittable_returns_zero_duration_marker(self) -> None:
        window_start = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
        # 30 min window, but task needs 60.
        tasks = [PersonalTask(
            task_id="t1", user_id="u_a", title="big",
            priority=1, estimated_minutes=60,
            category=TaskCategory.WORK,
        )]
        planner = TimeBlockPlanner()
        blocks = planner.plan(tasks, [], window_start=window_start, window_end=window_end)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].note, "unfittable")

    def test_busy_calendar_pushes_task_later(self) -> None:
        window_start = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        events = [CalendarEvent(
            user_id="u_a", uid="ev-1", summary="standup",
            start=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            end=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            kind=EventKind.VEVENT, status=EventStatus.CONFIRMED,
        )]
        tasks = [PersonalTask(
            task_id="t1", user_id="u_a", title="x", priority=1,
            estimated_minutes=30, category=TaskCategory.WORK,
        )]
        planner = TimeBlockPlanner()
        blocks = planner.plan(tasks, events, window_start=window_start, window_end=window_end)
        self.assertEqual(len(blocks), 1)
        self.assertGreaterEqual(blocks[0].start.hour, 10)


# ---------------------------------------------------------------------------
# v0.33 — Inbox
# ---------------------------------------------------------------------------


class ForwardedContentRouterTests(unittest.TestCase):
    def test_url_detection(self) -> None:
        msg = InboundMessage.new(
            channel="email", sender="me", body="check https://arxiv.org/abs/2510.04618",
        )
        decision = ForwardedContentRouter().classify(msg)
        self.assertEqual(decision.category, InboxCategory.URL)
        self.assertEqual(decision.recommended_operation, "capture_url")

    def test_pdf_url_routes_to_pdf(self) -> None:
        msg = InboundMessage.new(
            channel="email", sender="me",
            body="https://arxiv.org/pdf/2510.04618.pdf",
        )
        decision = ForwardedContentRouter().classify(msg)
        self.assertEqual(decision.category, InboxCategory.PDF)

    def test_ical_body_routes_to_calendar(self) -> None:
        msg = InboundMessage.new(
            channel="email", sender="me",
            body=(
                "BEGIN:VCALENDAR\nVERSION:2.0\n"
                "BEGIN:VEVENT\nUID:x\nDTSTART:20260601T140000Z\n"
                "DTEND:20260601T150000Z\nSUMMARY:Meeting\nEND:VEVENT\n"
                "END:VCALENDAR"
            ),
        )
        decision = ForwardedContentRouter().classify(msg)
        self.assertEqual(decision.category, InboxCategory.CALENDAR_INVITE)

    def test_task_language_routes_to_task(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="记得明天 9 点开会",
        )
        decision = ForwardedContentRouter().classify(msg)
        self.assertEqual(decision.category, InboxCategory.TASK)

    def test_empty_returns_empty(self) -> None:
        msg = InboundMessage.new(channel="cli", sender="me", body="")
        decision = ForwardedContentRouter().classify(msg)
        self.assertEqual(decision.category, InboxCategory.EMPTY)

    def test_fallback_to_wiki(self) -> None:
        msg = InboundMessage.new(
            channel="cli", sender="me",
            body="这个想法很有意思,大概就是 X Y Z 的方向",
        )
        decision = ForwardedContentRouter().classify(msg)
        self.assertEqual(decision.category, InboxCategory.WIKI)


# ---------------------------------------------------------------------------
# v0.34 — Projects
# ---------------------------------------------------------------------------


class ProjectStoreTests(unittest.TestCase):
    def test_create_and_get_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            p = store.create(user_id="u_a", title="ship v0.40")
            self.assertEqual(p.status, ProjectStatus.PENDING)
            fetched = store.get(p.project_id)
            self.assertEqual(fetched.title, "ship v0.40")

    def test_attach_plan_transitions_to_planning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            p = store.create(user_id="u_a", title="x")
            updated = store.attach_plan(
                p.project_id,
                plan_markdown="# Plan\n1. step",
                plan_proposal_id="prop_1",
            )
            self.assertEqual(updated.status, ProjectStatus.PLANNING)
            self.assertEqual(updated.plan_proposal_id, "prop_1")

    def test_subtask_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            p = store.create(user_id="u_a", title="x")
            s1 = store.add_subtask(project_id=p.project_id, title="step 1")
            s2 = store.add_subtask(
                project_id=p.project_id, title="step 2",
                depends_on=[s1.subtask_id],
            )
            subtasks = store.list_subtasks(p.project_id)
            self.assertEqual(len(subtasks), 2)
            second = next(t for t in subtasks if t.title == "step 2")
            self.assertEqual(second.depends_on, [s1.subtask_id])

    def test_link_worker_task_transitions_subtask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            p = store.create(user_id="u_a", title="x")
            s = store.add_subtask(project_id=p.project_id, title="step")
            updated = store.link_worker_task(s.subtask_id, "task_abc")
            self.assertEqual(updated.worker_task_id, "task_abc")
            self.assertEqual(updated.status, "in_progress")


# ---------------------------------------------------------------------------
# v0.35 — PPTX
# ---------------------------------------------------------------------------


class PPTXOutlineTests(unittest.TestCase):
    def test_deck_outline_serialisation(self) -> None:
        deck = DeckOutline(
            title="ACE Architecture",
            audience="self",
            theme="academic",
            slides=[
                Slide(title="Overview",
                      bullets=[Bullet(text="3-tier memory"),
                               Bullet(text="bitemporal", level=1)],
                      speaker_notes="emphasise persistence"),
            ],
        )
        d = deck.to_dict()
        self.assertEqual(d["title"], "ACE Architecture")
        self.assertEqual(len(d["slides"]), 1)
        self.assertEqual(deck.slide_count(), 1)

    def test_stub_builder_unavailable_without_binary(self) -> None:
        builder = StubPPTXBuilder()
        # `pptx-omni` binary is not on PATH in the test env.
        self.assertFalse(builder.available())
        with self.assertRaises(NotImplementedError):
            builder.render(
                DeckOutline(title="x"), Path("/tmp/never.pptx"),
            )


# ---------------------------------------------------------------------------
# v0.36 — Finance ops
# ---------------------------------------------------------------------------


class OrderIntentTests(unittest.TestCase):
    def test_factory_validates_qty(self) -> None:
        with self.assertRaises(ValueError):
            OrderIntent.new(
                user_id="u_a", instrument="NVDA",
                side=OrderSide.BUY, qty=-1,
            )

    def test_limit_requires_limit_price(self) -> None:
        with self.assertRaises(ValueError):
            OrderIntent.new(
                user_id="u_a", instrument="NVDA",
                side=OrderSide.BUY, qty=1,
                order_type=OrderType.LIMIT,
            )

    def test_factory_emits_valid_limit_intent(self) -> None:
        intent = OrderIntent.new(
            user_id="u_a", instrument="NVDA",
            side=OrderSide.BUY, qty=10,
            order_type=OrderType.LIMIT, limit_price=195.0,
        )
        self.assertEqual(intent.side, OrderSide.BUY)
        self.assertEqual(intent.limit_price, 195.0)


class RiskCheckTests(unittest.TestCase):
    def test_blocks_oversized_position(self) -> None:
        intent = OrderIntent.new(
            user_id="u_a", instrument="NVDA",
            side=OrderSide.BUY, qty=100,
            order_type=OrderType.LIMIT, limit_price=300.0,
        )
        result = risk_check(intent, portfolio_value_usd=50_000.0)
        # 100 * 300 = 30,000 vs 50,000 portfolio = 60% → hard block.
        self.assertFalse(result.passes)
        self.assertTrue(result.hard_blocks)

    def test_warns_at_threshold(self) -> None:
        intent = OrderIntent.new(
            user_id="u_a", instrument="NVDA",
            side=OrderSide.BUY, qty=10,
            order_type=OrderType.LIMIT, limit_price=300.0,
        )
        # 10 * 300 = 3000 vs 25000 portfolio = 12% → warn.
        result = risk_check(intent, portfolio_value_usd=25_000.0)
        self.assertTrue(result.passes)
        self.assertTrue(result.warnings)

    def test_market_buy_without_price_blocks(self) -> None:
        intent = OrderIntent.new(
            user_id="u_a", instrument="NVDA",
            side=OrderSide.BUY, qty=1,
            order_type=OrderType.MARKET,
        )
        result = risk_check(intent, portfolio_value_usd=10_000.0)
        self.assertFalse(result.passes)


class FinanceAnalystTests(unittest.TestCase):
    def test_alert_create_then_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            analyst = FinanceAnalyst(tmp)
            rule = AlertRule(
                rule_id="al-1", user_id="u_a",
                instrument="NVDA", expression="price > 200",
            )
            analyst.watch_create(rule)
            listed = analyst.list_alerts(user_id="u_a")
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].instrument, "NVDA")

    def test_portfolio_stats_empty_when_no_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            analyst = FinanceAnalyst(tmp)
            snap = analyst.portfolio_stats("u_a")
            self.assertEqual(snap.snapshot_id, "empty-0")
            self.assertEqual(snap.total_value_usd, 0.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
