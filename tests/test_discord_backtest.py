from __future__ import annotations

import hashlib
from datetime import datetime
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from omni_hub.discord_backtest import (
    CURATION_MANIFEST_SHA256,
    QUANT_PYTHON,
    run_quant_blogger_backtest,
)


_SOURCE_FIELDS = {
    "confidence",
    "direction",
    "duplicate_of",
    "effective_at",
    "entry",
    "entry_high",
    "entry_low",
    "evaluable",
    "exclusion_reason",
    "explicit_reference_ids",
    "link_basis",
    "open_message_id",
    "parameter_fingerprint",
    "profile",
    "sl",
    "symbol",
    "tps",
}
_PROFILE_COUNTS = {
    "always-win-trader": 15,
    "analyst-nick": 2,
    "coin-chief-v1": 58,
    "shuqin-v1": 141,
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class DiscordBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.market_root = self.root / "market"
        self.output_dir = self.root / "published" / "backtest"
        self.event_root = self.root / "events"
        self.event_root.mkdir()
        self.market_root.mkdir()
        self.curation_manifest = self.root / "curation.json"
        self._write_event_bundle()
        self._write_curation_bundle()
        self._write_market_bundle()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    @staticmethod
    def _selected_row(index: int, profile: str) -> dict[str, object]:
        symbol = "BTCUSDT" if index % 2 == 0 else "ETHUSDT"
        entry = 60_000.0 if symbol == "BTCUSDT" else 2_000.0
        row = {
            "confidence": "high",
            "direction": "long",
            "duplicate_of": None,
            "effective_at": "2026-07-12T00:00:00Z",
            "entry": entry,
            "entry_high": entry,
            "entry_low": entry,
            "evaluable": True,
            "exclusion_reason": None,
            "explicit_reference_ids": [str(index + 1)],
            "link_basis": "initial_plan",
            "open_message_id": str(index + 1),
            "parameter_fingerprint": hashlib.sha256(str(index).encode()).hexdigest(),
            "profile": profile,
            "sl": entry * 0.99,
            "symbol": symbol,
            "tps": [entry * 1.01],
        }
        assert set(row) == _SOURCE_FIELDS
        return row

    def _write_event_bundle(self) -> None:
        files: dict[str, str] = {}
        for name in (
            "latest-calls.json",
            "latest-calls.md",
            "message-decisions.jsonl",
            "trade-events.jsonl",
            "trade-lifecycles.jsonl",
        ):
            content = (name + "\n").encode()
            (self.event_root / name).write_bytes(content)
            files[name] = hashlib.sha256(content).hexdigest()
        self.event_manifest_data = {
            "artifact_kind": "discord-blogger-events-v1",
            "decision_count": 9414,
            "event_count": 2857,
            "files": files,
            "latest_call_count": 96,
            "lifecycle_count": 1477,
            "provenance": {
                "asof": "2026-07-21T00:00:00+00:00",
                "closure_audit": {
                    "input_file_sha256": {
                        "census": "a" * 64,
                        "head_catchup": "b" * 64,
                        "merge_audit": "c" * 64,
                    },
                    "path": "closure/audit.json",
                    "sha256": "d" * 64,
                },
                "corpus_commitment": "e" * 64,
                "corpus_message_count": 9414,
                "parser_implementation_sha256": "f" * 64,
                "profiles": [],
            },
        }
        self.event_manifest = self.event_root / "event-manifest.json"
        self.event_manifest.write_bytes(_canonical(self.event_manifest_data) + b"\n")

    def _write_curation_bundle(self) -> None:
        selected: list[dict[str, object]] = []
        cursor = 0
        for profile, count in _PROFILE_COUNTS.items():
            for _ in range(count):
                selected.append(self._selected_row(cursor, profile))
                cursor += 1
        excluded = []
        for index in range(108):
            row = self._selected_row(cursor + index, "shuqin-v1")
            row["evaluable"] = False
            row["confidence"] = "low"
            row["exclusion_reason"] = "ambiguous"
            excluded.append(row)
        self.source_a = self.root / "curated-a.jsonl"
        self.source_b = self.root / "curated-b.jsonl"
        self.source_a.write_bytes(b"".join(_canonical(row) + b"\n" for row in selected[:162]))
        self.source_b.write_bytes(
            b"".join(_canonical(row) + b"\n" for row in selected[162:] + excluded)
        )
        manifest = {
            "schema_version": "discord-blogger-curation-v1",
            "methodology": "initial_plan_levels_only",
            "asof": "2026-07-21T00:00:00Z",
            "market_bar_end_exclusive": "2026-07-13T02:50:00Z",
            "event_manifest": {
                "path": str(self.event_manifest),
                "sha256": hashlib.sha256(self.event_manifest.read_bytes()).hexdigest(),
            },
            "sources": [
                {
                    "path": str(self.source_a),
                    "sha256": hashlib.sha256(self.source_a.read_bytes()).hexdigest(),
                    "row_count": 162,
                },
                {
                    "path": str(self.source_b),
                    "sha256": hashlib.sha256(self.source_b.read_bytes()).hexdigest(),
                    "row_count": 162,
                },
            ],
            "selection": {
                "evaluable": True,
                "confidence": "high",
                "duplicate_of": None,
                "exclusion_reason": None,
                "excluded_terminal_prefixes": ["cancelled_", "expired_"],
                "input_row_count": 324,
                "selected_row_count": 216,
                "selected_profile_counts": _PROFILE_COUNTS,
            },
            "limitations": [
                "funding_unmodeled",
                "media_semantics_pending_for_three_related_jpeg_occurrences",
            ],
        }
        self.curation_manifest.write_bytes(_canonical(manifest) + b"\n")
        self.curation_sha = hashlib.sha256(self.curation_manifest.read_bytes()).hexdigest()

    def _write_market_bundle(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            for date in ("2026-07-12", "2026-07-13"):
                folder = self.market_root / "bars_1m" / f"symbol={symbol}" / f"date={date}"
                folder.mkdir(parents=True)
                (folder / f"{symbol}-{date}.parquet").write_bytes(
                    f"{symbol}:{date}".encode()
                )
        ignored = self.market_root / "bars_1m" / "symbol=BTCUSDT" / "date=2026-07-11"
        ignored.mkdir(parents=True)
        (ignored / "ignored.parquet").write_bytes(b"ignored")

    def _core_result(self, command: list[str]) -> dict[str, object]:
        lifecycle_path = Path(command[command.index("--lifecycles") + 1])
        lifecycle_bytes = lifecycle_path.read_bytes()
        rows = [json.loads(line) for line in lifecycle_bytes.splitlines() if line]
        market_manifest_path = Path(command[command.index("--market-input-manifest") + 1])
        market_manifest_bytes = market_manifest_path.read_bytes()
        market_manifest = json.loads(market_manifest_bytes)
        start_us = 1_783_814_460_000_000
        end_us = 1_783_911_000_000_000
        trades = []
        for index, row in enumerate(rows):
            outcome = "win" if index % 2 == 0 else "loss"
            signal_us = int(
                datetime.fromisoformat(row["effective_at"].replace("Z", "+00:00")).timestamp()
                * 1_000_000
            )
            target = row["tps"][0]
            trades.append(
                {
                    "lifecycle_id": row["open_message_id"],
                    "profile": row["profile"],
                    "symbol": row["symbol"],
                    "direction": row["direction"],
                    "signal_at_us": signal_us,
                    "status": "closed",
                    "outcome": outcome,
                    "exclusion_reason": None,
                    "entry_bucket_ts": start_us,
                    "entry_price": row["entry"],
                    "exit_bucket_ts": start_us + 60_000_000,
                    "exit_reason": "take_profit" if outcome == "win" else "stop_loss",
                    "tp_fills": [] if outcome == "loss" else [{
                        "target": target,
                        "fraction": 1.0,
                        "exit_price": target,
                        "bucket_ts": start_us + 60_000_000,
                    }],
                    "remaining_fraction": 0.0,
                    "gross_return_pct": 1.0 if outcome == "win" else -1.0,
                    "fees_pct": 0.08,
                    "slippage_pct": 0.02,
                    "net_return_pct": 0.92 if outcome == "win" else -1.08,
                }
            )
        wins = sum(row["outcome"] == "win" for row in trades)
        losses = len(trades) - wins
        return {
            "schema_version": "discord-backtest-v1",
            "bar_interval": "1m",
            "funding": "unmodeled",
            "input_binding": {
                "sha256": hashlib.sha256(lifecycle_bytes).hexdigest(),
                "count": len(rows),
            },
            "market_input_binding": {
                "manifest_sha256": hashlib.sha256(market_manifest_bytes).hexdigest(),
                "file_count": len(market_manifest["files"]),
                "files_aggregate_sha256": market_manifest["files_aggregate_sha256"],
            },
            "runtime_binding": {
                "python_version": "3.12.test",
                "pyarrow_version": "test",
                "implementation_sha256": hashlib.sha256(
                    Path("agent-harness/quant/quant/discord_backtest.py").read_bytes()
                ).hexdigest(),
            },
            "market_window": {
                "requested_start_us": start_us,
                "requested_end_us": end_us,
                "symbols": {
                    symbol: {
                        "bar_count": (end_us - start_us) // 60_000_000,
                        "first_bucket_ts": start_us,
                        "last_bucket_ts": end_us - 60_000_000,
                    }
                    for symbol in ("BTCUSDT", "ETHUSDT")
                },
            },
            "parameters": {
                "fee_bps": 4.0,
                "slippage_bps": 1.0,
                "max_entry_wait_minutes": 1440,
            },
            "summary": {
                "lifecycles": len(trades),
                "closed": len(trades),
                "wins": wins,
                "losses": losses,
                "flat": 0,
                "unfilled": 0,
                "right_censored": 0,
                "excluded": 0,
                "win_rate": wins / (wins + losses),
            },
            "trades": trades,
        }

    def _run(self, **overrides: object) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "curation_manifest": self.curation_manifest,
            "curation_manifest_sha256": self.curation_sha,
            "market_root": self.market_root,
            "output_dir": self.output_dir,
            "fee_bps": 4.0,
            "slippage_bps": 1.0,
            "max_entry_wait_minutes": 1440,
            "timeout_seconds": 30,
        }
        kwargs.update(overrides)

        def fake_run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
            self.assertEqual(command[0], str(QUANT_PYTHON))
            self.assertEqual(options["timeout"], 30)
            result = self._core_result(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        with patch("omni_hub.discord_backtest.subprocess.run", side_effect=fake_run):
            return run_quant_blogger_backtest(**kwargs)  # type: ignore[arg-type]

    def test_publishes_hash_bound_curated_backtest_and_market_inventory(self) -> None:
        result = self._run()

        self.assertEqual(result["selected_lifecycle_count"], 216)
        expected_files = {
            "backtest-manifest.json",
            "backtest-report.json",
            "backtest-report.md",
            "curated-lifecycles.jsonl",
            "curation-input.jsonl",
            "market-input-manifest.json",
            "trades.jsonl",
        }
        self.assertEqual({path.name for path in self.output_dir.iterdir()}, expected_files)
        manifest = json.loads((self.output_dir / "backtest-manifest.json").read_text())
        self.assertEqual(manifest["artifact_kind"], "discord-blogger-backtest-v1")
        self.assertEqual(set(manifest["files"]), expected_files - {"backtest-manifest.json"})
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.output_dir / name).read_bytes()).hexdigest(), digest)
        market = json.loads((self.output_dir / "market-input-manifest.json").read_text())
        self.assertEqual(market["schema_version"], "market-input-request-v1")
        self.assertEqual(len(market["files"]), 4)
        self.assertEqual(market["market_root"], str(self.market_root.resolve()))
        self.assertEqual(
            hashlib.sha256(_canonical([
                [row["path"], row["size_bytes"], row["sha256"]]
                for row in market["files"]
            ])).hexdigest(),
            market["files_aggregate_sha256"],
        )
        curation_rows = [
            json.loads(line)
            for line in (self.output_dir / "curation-input.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(curation_rows), 324)
        report = json.loads((self.output_dir / "backtest-report.json").read_text())
        self.assertEqual(report["profiles"]["shuqin-v1"]["input_rows"], 249)
        self.assertEqual(report["profiles"]["shuqin-v1"]["curation_excluded"], 108)
        self.assertEqual(
            sum(report["profiles"]["shuqin-v1"]["curation_excluded_by_reason"].values()),
            108,
        )
        self.assertEqual(report["profiles"]["shuqin-v1"]["selected"], 141)
        self.assertEqual(report["profiles"]["shuqin-v1"]["simulated"], 141)
        self.assertEqual(report["profiles"]["coin-chief-v1"]["closed"], 58)
        self.assertEqual(report["profiles"]["coin-chief-v1"]["slippage_bps_assumption"], 1.0)
        self.assertGreater(report["profiles"]["coin-chief-v1"]["fees_pct_total"], 0)
        self.assertEqual(report["methodology"]["funding"], "unmodeled")
        self.assertEqual(report["methodology"]["ttl_minutes"], 1440)
        self.assertEqual(report["methodology"]["entry_wait_anchor"], "effective_at")
        self.assertEqual(
            report["methodology"]["deadline_bar_policy"],
            "full_bar_must_close_by_deadline",
        )
        self.assertEqual(report["profiles"]["analyst-nick"]["win_rate_denominator"], 2)
        self.assertEqual(report["conservation"], {
            "input_rows": 324,
            "curation_excluded": 108,
            "simulated": 216,
            "closed": 216,
            "unfilled": 0,
            "right_censored": 0,
            "quant_excluded": 0,
        })
        markdown = (self.output_dir / "backtest-report.md").read_text()
        for heading in ("Input", "Curated excluded", "Unfilled", "Right-censored", "Quant excluded"):
            self.assertIn(heading, markdown)
        self.assertRegex(manifest["implementation"]["wrapper_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["implementation"]["quant_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(manifest["runtime"]["quant"]["python_version"], "3.12.test")
        self.assertEqual(stat_mode(self.output_dir), 0o700)
        for path in self.output_dir.iterdir():
            self.assertEqual(stat_mode(path), 0o600)
        combined = b"".join(path.read_bytes() for path in self.output_dir.iterdir())
        self.assertNotIn(b"content", combined)
        self.assertNotIn(b"logical_key", combined)
        self.assertNotIn(b"Authorization", combined)

    def test_rejects_wrong_manifest_commitment_before_subprocess_or_publication(self) -> None:
        with patch("omni_hub.discord_backtest.subprocess.run") as execute:
            with self.assertRaisesRegex(ValueError, "curation manifest commitment"):
                run_quant_blogger_backtest(
                    curation_manifest=self.curation_manifest,
                    curation_manifest_sha256="0" * 64,
                    market_root=self.market_root,
                    output_dir=self.output_dir,
                    fee_bps=4.0,
                    slippage_bps=1.0,
                )
        execute.assert_not_called()
        self.assertFalse(self.output_dir.exists())

    def test_rejects_source_schema_or_sensitive_field(self) -> None:
        row = json.loads(self.source_a.read_text().splitlines()[0])
        row["content"] = "secret body"
        lines = self.source_a.read_text().splitlines()
        lines[0] = json.dumps(row)
        self.source_a.write_text("\n".join(lines) + "\n")
        manifest = json.loads(self.curation_manifest.read_text())
        manifest["sources"][0]["sha256"] = hashlib.sha256(self.source_a.read_bytes()).hexdigest()
        self.curation_manifest.write_bytes(_canonical(manifest) + b"\n")
        digest = hashlib.sha256(self.curation_manifest.read_bytes()).hexdigest()

        with patch("omni_hub.discord_backtest.subprocess.run") as execute:
            with self.assertRaisesRegex(ValueError, "source row schema"):
                run_quant_blogger_backtest(
                    curation_manifest=self.curation_manifest,
                    curation_manifest_sha256=digest,
                    market_root=self.market_root,
                    output_dir=self.output_dir,
                    fee_bps=4.0,
                    slippage_bps=1.0,
                )
        execute.assert_not_called()

    def test_accepts_reviewed_non_hash_parameter_fingerprint_as_opaque_data(self) -> None:
        lines = self.source_a.read_text().splitlines()
        row = json.loads(lines[0])
        row["parameter_fingerprint"] = "BTCUSDT|long|60000|59400|60600"
        lines[0] = json.dumps(row, ensure_ascii=False, sort_keys=True)
        self.source_a.write_text("\n".join(lines) + "\n")
        manifest = json.loads(self.curation_manifest.read_text())
        manifest["sources"][0]["sha256"] = hashlib.sha256(self.source_a.read_bytes()).hexdigest()
        self.curation_manifest.write_bytes(_canonical(manifest) + b"\n")
        self.curation_sha = hashlib.sha256(self.curation_manifest.read_bytes()).hexdigest()

        result = self._run()

        self.assertEqual(result["selected_lifecycle_count"], 216)

    def test_rejects_symlinked_market_file(self) -> None:
        target = next(self.market_root.rglob("*.parquet"))
        real = self.root / "outside.parquet"
        real.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(real)
        with self.assertRaisesRegex(ValueError, "market.*symlink"):
            self._run()
        self.assertFalse(self.output_dir.exists())

    def test_rejects_core_input_binding_or_market_window_mismatch(self) -> None:
        def fake_run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
            result = self._core_result(command)
            result["input_binding"]["sha256"] = "0" * 64  # type: ignore[index]
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        with patch("omni_hub.discord_backtest.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(ValueError, "input binding"):
                run_quant_blogger_backtest(
                    curation_manifest=self.curation_manifest,
                    curation_manifest_sha256=self.curation_sha,
                    market_root=self.market_root,
                    output_dir=self.output_dir,
                    fee_bps=4.0,
                    slippage_bps=1.0,
                )
        self.assertFalse(self.output_dir.exists())

    def test_rejects_market_input_binding_mismatch(self) -> None:
        def fake_run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
            result = self._core_result(command)
            result["market_input_binding"]["files_aggregate_sha256"] = "0" * 64  # type: ignore[index]
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        with patch("omni_hub.discord_backtest.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(ValueError, "market input binding"):
                run_quant_blogger_backtest(
                    curation_manifest=self.curation_manifest,
                    curation_manifest_sha256=self.curation_sha,
                    market_root=self.market_root,
                    output_dir=self.output_dir,
                    fee_bps=4.0,
                    slippage_bps=1.0,
                )
        self.assertFalse(self.output_dir.exists())

    def test_rejects_malformed_quant_trade_rows_without_echoing_values(self) -> None:
        mutations = {
            "unknown status": lambda row: row.update(status="bogus"),
            "invalid remaining": lambda row: row.update(remaining_fraction=99.0),
            "negative fees": lambda row: row.update(fees_pct=-100.0),
            "signal mismatch": lambda row: row.update(signal_at_us=1),
            "exit before entry": lambda row: row.update(exit_bucket_ts=1),
            "outcome conflict": lambda row: row.update(outcome=None),
            "exit reason": lambda row: row.update(exit_reason="mystery"),
            "stop after full target": lambda row: row.update(exit_reason="stop_loss"),
            "target on entry bar": lambda row: row["tp_fills"][0].update(  # type: ignore[index]
                bucket_ts=row["entry_bucket_ts"]
            ),
            "bad tp fill": lambda row: row.update(tp_fills=[{"target": 1.0}]),
            "nonnumeric private": lambda row: row.update(net_return_pct="PRIVATE_SENTINEL"),
        }
        for index, (label, mutate) in enumerate(mutations.items()):
            with self.subTest(label=label):
                output = self.root / f"invalid-{index}"

                def fake_run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
                    result = copy.deepcopy(self._core_result(command))
                    mutate(result["trades"][0])  # type: ignore[index]
                    return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

                with patch("omni_hub.discord_backtest.subprocess.run", side_effect=fake_run):
                    with self.assertRaisesRegex(ValueError, "quant trade row") as raised:
                        run_quant_blogger_backtest(
                            curation_manifest=self.curation_manifest,
                            curation_manifest_sha256=self.curation_sha,
                            market_root=self.market_root,
                            output_dir=output,
                            fee_bps=4.0,
                            slippage_bps=1.0,
                        )
                self.assertNotIn("PRIVATE_SENTINEL", str(raised.exception))
                self.assertFalse(output.exists())

    def test_rejects_curation_mutation_during_subprocess(self) -> None:
        def fake_run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
            result = self._core_result(command)
            self.curation_manifest.write_bytes(self.curation_manifest.read_bytes() + b" ")
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        with patch("omni_hub.discord_backtest.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(ValueError, "curation.*changed"):
                run_quant_blogger_backtest(
                    curation_manifest=self.curation_manifest,
                    curation_manifest_sha256=self.curation_sha,
                    market_root=self.market_root,
                    output_dir=self.output_dir,
                    fee_bps=4.0,
                    slippage_bps=1.0,
                )
        self.assertFalse(self.output_dir.exists())

    def test_subprocess_failures_are_redacted_and_output_is_no_clobber(self) -> None:
        secret = "https://cdn.discordapp.com/private?ex=secret"
        with patch(
            "omni_hub.discord_backtest.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["quant"], 1, stderr=secret),
        ):
            with self.assertRaisesRegex(RuntimeError, "timed out") as raised:
                run_quant_blogger_backtest(
                    curation_manifest=self.curation_manifest,
                    curation_manifest_sha256=self.curation_sha,
                    market_root=self.market_root,
                    output_dir=self.output_dir,
                    fee_bps=4.0,
                    slippage_bps=1.0,
                    timeout_seconds=1,
                )
        self.assertNotIn(secret, str(raised.exception))
        self.assertFalse(self.output_dir.exists())

        self._run()
        sentinel = (self.output_dir / "backtest-manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self._run()
        self.assertEqual((self.output_dir / "backtest-manifest.json").read_bytes(), sentinel)

    def test_allows_only_known_arrow_sandbox_probe_stderr(self) -> None:
        known = (
            "/build/arrow/cpp/src/arrow/util/cpu_info.cc:239: IOError: "
            "sysctlbyname failed for 'hw.l1dcachesize'. Detail: [errno 1] Operation not permitted\n"
        )

        def fake_run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command, 0, json.dumps(self._core_result(command)), known
            )

        with patch("omni_hub.discord_backtest.subprocess.run", side_effect=fake_run):
            result = run_quant_blogger_backtest(
                curation_manifest=self.curation_manifest,
                curation_manifest_sha256=self.curation_sha,
                market_root=self.market_root,
                output_dir=self.output_dir,
                fee_bps=4.0,
                slippage_bps=1.0,
            )
        self.assertEqual(result["closed_trade_count"], 216)

        other = "warning includes https://example.invalid/private?token=secret"
        with patch(
            "omni_hub.discord_backtest.subprocess.run",
            return_value=subprocess.CompletedProcess(["quant"], 0, "{}", other),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected stderr") as raised:
                run_quant_blogger_backtest(
                    curation_manifest=self.curation_manifest,
                    curation_manifest_sha256=self.curation_sha,
                    market_root=self.market_root,
                    output_dir=self.root / "other-output",
                    fee_bps=4.0,
                    slippage_bps=1.0,
                )
        self.assertNotIn(other, str(raised.exception))

    def test_post_rename_failure_quarantines_exact_publication(self) -> None:
        from omni_hub import discord_backtest

        original = discord_backtest._verify_stage
        calls = 0

        def fail_second_verification(**kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected post-rename verification failure")
            original(**kwargs)  # type: ignore[arg-type]

        with patch("omni_hub.discord_backtest._verify_stage", side_effect=fail_second_verification):
            with self.assertRaisesRegex(OSError, "post-rename"):
                self._run()

        self.assertFalse(self.output_dir.exists())
        quarantines = list(self.output_dir.parent.glob(".backtest.quarantine-*"))
        self.assertEqual(len(quarantines), 1)
        self.assertEqual(stat_mode(quarantines[0]), 0o700)
        self.assertTrue((quarantines[0] / "backtest-manifest.json").is_file())

    def test_formal_manifest_sha_constant_is_exact(self) -> None:
        self.assertEqual(
            CURATION_MANIFEST_SHA256,
            "18aa1a96c8956bd0c74bc53d0d9355858bc080c8b74873b49bf6fe04eaf863c1",
        )


def stat_mode(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
