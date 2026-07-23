from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest

from omni_hub.discord_media_audit import (
    MEDIA_RECOVERY_AUDIT_FILENAME,
    MEDIA_RECOVERY_AUDIT_KIND,
    MEDIA_RECOVERY_AUDIT_VERSION,
    build_media_recovery_audit,
    canonical_media_recovery_audit_bytes,
    rebuild_media_recovery_counts,
    rebuild_media_recovery_rows,
)
from omni_hub.discord_media_recovery import (
    LEGACY_RETRY_TRIGGER,
    RESOLUTION_RETRY_TRIGGER,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
ROW_FIELDS = {
    "row_id",
    "item_kind",
    "logical_key",
    "candidate_url_sha256",
    "candidate_host",
    "attempt_number",
    "retry_trigger",
    "evidence_reclassification",
    "status",
    "terminal_reason",
    "failure_detail",
    "actual_bytes",
    "binary_captured",
    "final_record_status",
    "final_record_terminal_reason",
    "disposition",
}
COUNT_KEYS = {
    "rows_total",
    "attempt_rows",
    "record_rows",
    "legacy_attempt_rows",
    "legacy_zero_byte_reclassification_rows",
    "typed_resolution_attempt_rows",
    "http_400_404_415_attempt_rows",
    "binary_captured_attempt_rows",
    "candidate_failed_record_covered_attempt_rows",
    "current_failed_records",
    "current_reference_only_records",
    "resolution_retry_pending_records",
    "resolution_retry_exhausted_records",
    "resolution_unresolved_records",
    "resolution_invalid_answer_records",
    "unsafe_records",
    "http_compensation_records",
    "hard_media_failure_records",
    "other_media_failure_records",
    "unresolved_blockers",
}


def _blob_path(digest: str, extension: str = "bin") -> str:
    return f"assets/sha256/{digest[:2]}/{digest}.{extension}"


def _attempt(
    url: str,
    *,
    status: str = "failed",
    terminal_reason: str | None = "download_http_404",
    failure_detail: str | None = None,
    retry_trigger: str | None = None,
    actual_bytes: int = 0,
    sha256: str | None = None,
    blob_path: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "url": url,
        "status": status,
        "terminal_reason": terminal_reason,
        "failure_detail": failure_detail,
        "actual_bytes": actual_bytes,
        "sha256": sha256,
        "blob_path": blob_path,
    }
    if retry_trigger is not None:
        value["retry_trigger"] = retry_trigger
    return value


def _record(
    logical_key: str,
    *,
    url: str | None = None,
    status: str = "failed",
    terminal_reason: str | None = "download_http_404",
    actual_bytes: int = 0,
    sha256: str | None = None,
    blob_path: str | None = None,
    attempts: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    candidate_url = url or f"https://cdn.discordapp.com/{logical_key}"
    if (
        status == "reference_only"
        and terminal_reason == "media_reference_not_binary"
        and actual_bytes == 0
        and sha256 is None
        and blob_path is None
    ):
        actual_bytes = 1
        sha256 = SHA_A
        blob_path = _blob_path(SHA_A)
    kind = "attachment"
    field = "attachment"
    content_type: str | None = None
    declared_content_type: str | None = None
    declared_size = actual_bytes
    if status == "captured_with_warning":
        if terminal_reason == "declared_size_mismatch":
            declared_size = actual_bytes + 1
        elif terminal_reason == "mime_mismatch":
            content_type = "application/octet-stream"
            declared_content_type = "application/json"
        elif terminal_reason == "media_type_unverified":
            kind = "embed"
            field = "video"
    elif status == "reference_only" and terminal_reason == "media_reference_not_binary":
        kind = "embed"
        field = "video"
        content_type = "text/html"
    metadata: dict[str, object] = (
        {
            "id": logical_key,
            "size": declared_size,
            "content_type": declared_content_type,
        }
        if kind == "attachment"
        else {"url": candidate_url, "proxy_url": None}
    )
    identity_metadata = (
        {
            "id": logical_key,
            "size": declared_size,
            "content_type": declared_content_type,
        }
        if kind == "attachment"
        else {}
    )
    generated_attempts = attempts
    source = {
        "message_id": "1",
        "channel_id": "2",
        "stream": "messages_2",
    }
    if generated_attempts is None and (
        status in {"complete", "captured_with_warning", "reference_only"}
        or (
            status == "failed"
            and terminal_reason
            not in {"logical_identity_conflict", "byte_transport_unavailable"}
        )
    ):
        generated_failure_detail = {
            "media_resolution_failed_transient": "resolver_timeout",
            "media_resolution_retry_exhausted": "resolver_timeout",
            "media_resolution_unresolved": "resolver_no_data",
            "media_resolution_invalid_answer": "resolver_invalid_answer",
        }.get(terminal_reason)
        generated = _attempt(
            candidate_url,
            status=status,
            terminal_reason=terminal_reason,
            failure_detail=generated_failure_detail,
            actual_bytes=actual_bytes,
            sha256=sha256,
            blob_path=blob_path,
        )
        generated["http_content_type"] = content_type
        generated["http_content_length"] = (
            actual_bytes if content_type is not None else None
        )
        generated_attempts = [generated]
    return {
        "logical_key": logical_key,
        "kind": kind,
        "field": field,
        "url": candidate_url,
        "declared_metadata": metadata,
        "declared_content_type": declared_content_type,
        "identity_metadata": identity_metadata,
        "identity_conflicts": [],
        "sources": [source],
        "observations": [
            {
                "source": source,
                "url": candidate_url,
                "proxy_url": None,
                "metadata": metadata,
            }
        ],
        "status": status,
        "terminal_reason": terminal_reason,
        "http_content_type": content_type,
        "http_content_length": (
            actual_bytes if content_type is not None else None
        ),
        "actual_bytes": actual_bytes,
        "sha256": sha256,
        "blob_path": blob_path,
        "attempt_history": generated_attempts or [],
    }


def _rows_by_key(
    rows: list[dict[str, object]],
) -> dict[tuple[str, str, int | None], dict[str, object]]:
    return {
        (str(row["logical_key"]), str(row["item_kind"]), row["attempt_number"]): row
        for row in rows
    }


class MediaRecoveryAuditTests(unittest.TestCase):
    def test_public_constants_and_canonical_bytes_are_exact(self) -> None:
        self.assertEqual(MEDIA_RECOVERY_AUDIT_VERSION, 2)
        self.assertEqual(
            MEDIA_RECOVERY_AUDIT_KIND,
            "discord_media_resolution_recovery_audit",
        )
        self.assertEqual(MEDIA_RECOVERY_AUDIT_FILENAME, "media-recovery-audit.json")
        value = {"z": "雪", "a": [2, 1]}
        expected = '{"a":[2,1],"z":"雪"}'.encode("utf-8")
        self.assertEqual(canonical_media_recovery_audit_bytes(value), expected + b"\n")
        self.assertEqual(
            canonical_media_recovery_audit_bytes(value, newline=False), expected
        )

    def test_attempt_union_deduplicates_and_record_selection_is_disjoint(self) -> None:
        url = "https://cdn.discordapp.com/one"
        records = {
            "one": _record(
                "one",
                terminal_reason="download_failed_transient",
                attempts=[
                    _attempt(
                        url,
                        terminal_reason="media_resolution_failed_transient",
                        failure_detail="resolver_timeout",
                        retry_trigger=LEGACY_RETRY_TRIGGER,
                    ),
                    _attempt(url, terminal_reason="download_http_415"),
                    _attempt(url, terminal_reason="download_failed_transient"),
                ],
            ),
            "reference": _record(
                "reference",
                status="reference_only",
                terminal_reason="media_reference_not_binary",
            ),
            "progress": _record(
                "progress",
                url=url,
                status="in_progress",
                terminal_reason=None,
                attempts=[
                    _attempt(
                        url,
                        status="in_progress",
                        terminal_reason=None,
                        retry_trigger=RESOLUTION_RETRY_TRIGGER,
                    )
                ],
            ),
            "complete": _record(
                "complete",
                status="complete",
                terminal_reason="downloaded",
                actual_bytes=2,
                sha256=SHA_A,
                blob_path=_blob_path(SHA_A),
            ),
        }

        rows = rebuild_media_recovery_rows(records)
        identities = [
            (row["logical_key"], row["item_kind"], row["attempt_number"])
            for row in rows
        ]

        self.assertEqual(
            identities,
            [
                ("one", "attempt", 1),
                ("one", "attempt", 2),
                ("one", "record", None),
                ("progress", "attempt", 1),
                ("reference", "record", None),
            ],
        )
        self.assertEqual(len(identities), len(set(identities)))

    def test_row_identity_uses_exact_payload_and_one_based_attempt_number(self) -> None:
        records = {
            "identity": _record(
                "identity",
                url="https://cdn.discordapp.com/second",
                attempts=[
                    _attempt(
                        "https://cdn.discordapp.com/first",
                        terminal_reason="download_failed_transient",
                    ),
                    _attempt("https://cdn.discordapp.com/second"),
                ],
            )
        }

        rows = _rows_by_key(rebuild_media_recovery_rows(records))
        attempt_row = rows[("identity", "attempt", 2)]
        record_row = rows[("identity", "record", None)]
        attempt_identity = {
            "item_kind": "attempt",
            "logical_key": "identity",
            "attempt_number": 2,
        }
        record_identity = {"item_kind": "record", "logical_key": "identity"}

        self.assertEqual(
            attempt_row["row_id"],
            hashlib.sha256(
                canonical_media_recovery_audit_bytes(
                    attempt_identity,
                    newline=False,
                )
            ).hexdigest(),
        )
        self.assertEqual(
            record_row["row_id"],
            hashlib.sha256(
                canonical_media_recovery_audit_bytes(record_identity, newline=False)
            ).hexdigest(),
        )

    def test_rows_sort_by_utf8_bytes_then_kind_and_attempt_number(self) -> None:
        records = {
            key: _record(
                key,
                url=f"https://cdn.discordapp.com/{key}/2",
                attempts=[
                    _attempt(f"https://cdn.discordapp.com/{key}/1"),
                    _attempt(f"https://cdn.discordapp.com/{key}/2"),
                ],
            )
            for key in ["雪", "z", "é"]
        }

        rows = rebuild_media_recovery_rows(records)

        expected_keys = sorted(records, key=lambda item: item.encode("utf-8"))
        self.assertEqual(
            [(row["logical_key"], row["item_kind"], row["attempt_number"]) for row in rows],
            [
                identity
                for key in expected_keys
                for identity in [
                    (key, "attempt", 1),
                    (key, "attempt", 2),
                    (key, "record", None),
                ]
            ],
        )

    def test_signed_url_is_hashed_exactly_but_never_emitted(self) -> None:
        exact_url = (
            "https://CDN.DISCORDAPP.COM./asset.png?width=10&sig=synthetic&token=secret"
        )
        record = _record(
            "signed",
            url=exact_url,
            attempts=[_attempt(exact_url)],
        )

        audit = build_media_recovery_audit(
            run_id="run-1",
            request_sha256=SHA_A,
            policy_inputs_sha256=SHA_B,
            asset_index_sha256=SHA_C,
            records={"signed": record},
        )
        attempt_row = audit["items"][0]
        content = canonical_media_recovery_audit_bytes(audit)

        self.assertEqual(
            attempt_row["candidate_url_sha256"],
            hashlib.sha256(exact_url.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(attempt_row["candidate_host"], "cdn.discordapp.com")
        self.assertNotIn(b"sig=synthetic", content)
        self.assertNotIn(b"token=secret", content)
        self.assertNotIn(exact_url.encode("utf-8"), content)
        self.assertNotIn(b"declared_metadata", content)
        self.assertNotIn(b"authorization", content.lower())
        self.assertNotIn(b"exception", content.lower())

    def test_malformed_url_has_null_host_without_normalizing_its_hash(self) -> None:
        exact_url = "https://[broken?sig=synthetic"
        record = _record(
            "malformed-host",
            url=exact_url,
            attempts=[_attempt(exact_url)],
        )

        row = rebuild_media_recovery_rows({"malformed-host": record})[0]

        self.assertIsNone(row["candidate_host"])
        self.assertEqual(
            row["candidate_url_sha256"],
            hashlib.sha256(exact_url.encode("utf-8")).hexdigest(),
        )

    def test_query_like_or_malformed_hostname_is_never_emitted(self) -> None:
        adversarial_urls = [
            "https://sig=synthetic/path",
            "https://token secret/path",
            "https://sig%3Dsynthetic/path",
            "https://-leading.example/path",
            "https://trailing-.example/path",
            "https://double..label/path",
        ]
        records = {
            f"host-{index}": _record(
                f"host-{index}",
                url=url,
                attempts=[_attempt(url)],
            )
            for index, url in enumerate(adversarial_urls)
        }

        audit = build_media_recovery_audit(
            run_id="adversarial-hosts",
            request_sha256=SHA_A,
            policy_inputs_sha256=None,
            asset_index_sha256=SHA_B,
            records=records,
        )
        content = canonical_media_recovery_audit_bytes(audit)

        self.assertTrue(all(row["candidate_host"] is None for row in audit["items"]))
        self.assertNotIn(b"sig=synthetic", content)
        self.assertNotIn(b"token secret", content)

    def test_scoped_ipv6_host_is_hidden_without_changing_exact_url_hash(self) -> None:
        exact_url = "https://[fe80::1%scope-marker]/path?sig=synthetic"
        record = _record(
            "scoped-ipv6",
            url=exact_url,
            attempts=[_attempt(exact_url)],
        )

        rows = rebuild_media_recovery_rows({"scoped-ipv6": record})

        self.assertTrue(all(row["candidate_host"] is None for row in rows))
        self.assertTrue(
            all(
                row["candidate_url_sha256"]
                == hashlib.sha256(exact_url.encode("utf-8")).hexdigest()
                for row in rows
            )
        )
        self.assertNotIn(
            b"scope-marker",
            canonical_media_recovery_audit_bytes({"items": rows}),
        )

    def test_rows_have_only_fixed_fields_and_record_null_fields(self) -> None:
        record = _record(
            "fixed",
            attempts=[
                _attempt(
                    "https://cdn.discordapp.com/fixed",
                    retry_trigger=LEGACY_RETRY_TRIGGER,
                )
            ],
        )
        rows = _rows_by_key(rebuild_media_recovery_rows({"fixed": record}))

        for row in rows.values():
            self.assertEqual(set(row), ROW_FIELDS)
        record_row = rows[("fixed", "record", None)]
        self.assertIsNone(record_row["attempt_number"])
        self.assertIsNone(record_row["retry_trigger"])
        self.assertIsNone(record_row["failure_detail"])
        self.assertEqual(record_row["final_record_status"], "failed")
        self.assertEqual(
            record_row["final_record_terminal_reason"], "download_http_404"
        )

    def test_attempt_dispositions_follow_binding_priority(self) -> None:
        binary_url = "https://cdn.discordapp.com/binary"
        warning_url = "https://cdn.discordapp.com/warning"
        covered_url = "https://cdn.discordapp.com/covered"
        records = {
            "binary": _record(
                "binary",
                url=binary_url,
                status="complete",
                terminal_reason="downloaded",
                actual_bytes=3,
                sha256=SHA_A,
                blob_path=_blob_path(SHA_A, "png"),
                attempts=[
                    _attempt(
                        binary_url,
                        status="complete",
                        terminal_reason="downloaded",
                        retry_trigger=RESOLUTION_RETRY_TRIGGER,
                        actual_bytes=3,
                        sha256=SHA_A,
                        blob_path=_blob_path(SHA_A, "png"),
                    )
                ],
            ),
            "warning": _record(
                "warning",
                url=warning_url,
                status="captured_with_warning",
                terminal_reason="declared_size_mismatch",
                actual_bytes=4,
                sha256=SHA_B,
                blob_path=_blob_path(SHA_B, "jpg"),
                attempts=[
                    _attempt(
                        warning_url,
                        status="captured_with_warning",
                        terminal_reason="declared_size_mismatch",
                        retry_trigger=RESOLUTION_RETRY_TRIGGER,
                        actual_bytes=4,
                        sha256=SHA_B,
                        blob_path=_blob_path(SHA_B, "jpg"),
                    )
                ],
            ),
            "covered": _record(
                "covered",
                status="complete",
                terminal_reason="downloaded",
                actual_bytes=2,
                sha256=SHA_C,
                blob_path=_blob_path(SHA_C),
                attempts=[
                    _attempt(covered_url),
                    _attempt(
                        "https://cdn.discordapp.com/covered",
                        status="complete",
                        terminal_reason="downloaded",
                        actual_bytes=2,
                        sha256=SHA_C,
                        blob_path=_blob_path(SHA_C),
                    ),
                ],
            ),
            "typed-progress": _record(
                "typed-progress",
                url="https://cdn.discordapp.com/progress",
                status="in_progress",
                terminal_reason=None,
                attempts=[
                    _attempt(
                        "https://cdn.discordapp.com/progress",
                        status="in_progress",
                        terminal_reason=None,
                        retry_trigger=RESOLUTION_RETRY_TRIGGER,
                    )
                ],
            ),
            "typed-interrupted": _record(
                "typed-interrupted",
                url="https://cdn.discordapp.com/interrupted",
                status="in_progress",
                terminal_reason="interrupted",
                attempts=[
                    _attempt(
                        "https://cdn.discordapp.com/interrupted",
                        status="interrupted",
                        terminal_reason="interrupted",
                        retry_trigger=RESOLUTION_RETRY_TRIGGER,
                    )
                ],
            ),
            "transient": _record(
                "transient",
                terminal_reason="media_resolution_failed_transient",
                attempts=[
                    _attempt(
                        "https://cdn.discordapp.com/transient",
                        terminal_reason="media_resolution_failed_transient",
                        failure_detail="resolver_eai_again",
                    )
                ],
            ),
            "exhausted": _record(
                "exhausted",
                terminal_reason="media_resolution_retry_exhausted",
                attempts=[
                    _attempt(
                        "https://cdn.discordapp.com/exhausted",
                        terminal_reason="media_resolution_retry_exhausted",
                        failure_detail="resolver_timeout",
                    )
                ],
            ),
            "unresolved": _record(
                "unresolved",
                terminal_reason="media_resolution_unresolved",
                attempts=[
                    _attempt(
                        "https://cdn.discordapp.com/unresolved",
                        terminal_reason="media_resolution_unresolved",
                        failure_detail="resolver_no_data",
                    )
                ],
            ),
            "invalid": _record(
                "invalid",
                terminal_reason="media_resolution_invalid_answer",
                attempts=[
                    _attempt(
                        "https://cdn.discordapp.com/invalid",
                        terminal_reason="media_resolution_invalid_answer",
                        failure_detail="resolver_invalid_answer",
                    )
                ],
            ),
            "unsafe": _record("unsafe", terminal_reason="unsafe_media_url"),
            "http": _record("http", terminal_reason="download_http_400"),
            "hard": _record("hard", terminal_reason="size_limit_exceeded"),
            "media-hard": _record(
                "media-hard", terminal_reason="declared_media_type_mismatch"
            ),
            "other": _record("other", terminal_reason="download_failed_transient"),
        }

        rows = _rows_by_key(rebuild_media_recovery_rows(records))

        expected = {
            ("binary", "attempt", 1): "binary_captured",
            ("warning", "attempt", 1): "captured_with_warning",
            ("covered", "attempt", 1): "candidate_failed_record_covered",
            ("typed-progress", "attempt", 1): "resolution_retry_pending",
            ("typed-interrupted", "attempt", 1): "resolution_retry_pending",
            ("transient", "attempt", 1): "resolution_retry_pending",
            ("transient", "record", None): "resolution_retry_pending",
            ("exhausted", "record", None): "resolution_retry_exhausted_blocker",
            ("unresolved", "record", None): "resolution_unresolved_blocker",
            ("invalid", "record", None): "resolution_invalid_answer_blocker",
            ("unsafe", "record", None): "unsafe_blocker",
            ("http", "record", None): "http_compensation_blocker",
            ("hard", "record", None): "hard_media_failure_blocker",
            ("media-hard", "record", None): "hard_media_failure_blocker",
            ("other", "record", None): "other_media_failure_blocker",
        }
        for identity, disposition in expected.items():
            self.assertEqual(rows[identity]["disposition"], disposition, identity)
        self.assertTrue(rows[("binary", "attempt", 1)]["binary_captured"])
        self.assertTrue(rows[("warning", "attempt", 1)]["binary_captured"])
        self.assertFalse(rows[("typed-progress", "attempt", 1)]["binary_captured"])
        self.assertFalse(rows[("typed-interrupted", "attempt", 1)]["binary_captured"])
        self.assertNotIn(("typed-progress", "record", None), rows)
        self.assertNotIn(("typed-interrupted", "record", None), rows)

    def test_reference_only_is_never_binary(self) -> None:
        record = _record(
            "reference",
            status="reference_only",
            terminal_reason="media_reference_not_binary",
            actual_bytes=9,
            sha256=SHA_A,
            blob_path=_blob_path(SHA_A, "webm"),
        )

        reference_row = rebuild_media_recovery_rows({"reference": record})[0]

        self.assertEqual(reference_row["disposition"], "reference_only_not_binary")
        self.assertFalse(reference_row["binary_captured"])

    def test_binary_capture_requires_collector_blob_identity_shape(self) -> None:
        valid = _record(
            "valid-binary",
            status="complete",
            terminal_reason="downloaded",
            actual_bytes=1,
            sha256=SHA_A,
            blob_path=_blob_path(SHA_A, "png"),
            attempts=[
                _attempt(
                    "https://cdn.discordapp.com/valid-binary",
                    status="complete",
                    terminal_reason="downloaded",
                    retry_trigger=RESOLUTION_RETRY_TRIGGER,
                    actual_bytes=1,
                    sha256=SHA_A,
                    blob_path=_blob_path(SHA_A, "png"),
                )
            ],
        )
        invalid_paths = [
            "blob.bin",
            f"/assets/sha256/aa/{SHA_A}.bin",
            f"assets/sha256/aa/../{SHA_A}.bin",
            f"assets/sha256/bb/{SHA_A}.bin",
            f"assets/sha256/aa/{SHA_B}.bin",
            f"assets/sha256/aa/{SHA_A}",
            f"assets/sha256/aa/{SHA_A}.",
            f"assets/sha256/aa/{SHA_A}.bin/extra",
            f"assets/sha256/aa/{SHA_A}.\ud800",
            f"assets//sha256/aa/{SHA_A}.bin",
            f"assets\\sha256\\aa\\{SHA_A}.bin",
        ]

        row = rebuild_media_recovery_rows({"valid-binary": valid})[0]
        self.assertTrue(row["binary_captured"])
        for blob_path in invalid_paths:
            malformed = {**valid, "blob_path": blob_path}
            with self.subTest(blob_path=blob_path):
                with self.assertRaises(ValueError):
                    rebuild_media_recovery_rows({"valid-binary": malformed})

    def test_covered_outcome_rejects_failure_reasons_and_accepts_exact_warnings(
        self,
    ) -> None:
        url = "https://cdn.discordapp.com/binary-outcome"
        invalid_attempt = _attempt(
            url,
            status="complete",
            terminal_reason="download_http_404",
            actual_bytes=1,
            sha256=SHA_A,
            blob_path=_blob_path(SHA_A),
        )
        invalid_record = _record(
            "binary-outcome",
            url=url,
            status="complete",
            terminal_reason="download_http_404",
            actual_bytes=1,
            sha256=SHA_A,
            blob_path=_blob_path(SHA_A),
            attempts=[invalid_attempt],
        )

        with self.assertRaises(ValueError):
            build_media_recovery_audit(
                run_id="run-invalid-binary-outcome",
                request_sha256=SHA_A,
                policy_inputs_sha256=None,
                asset_index_sha256=SHA_B,
                records={"binary-outcome": invalid_record},
            )

        invalid_reference_attempt = _attempt(
            url,
            status="reference_only",
            terminal_reason="download_http_404",
            actual_bytes=1,
            sha256=SHA_A,
            blob_path=_blob_path(SHA_A),
        )
        invalid_reference_record = _record(
            "binary-outcome",
            url=url,
            status="reference_only",
            terminal_reason="download_http_404",
            actual_bytes=1,
            sha256=SHA_A,
            blob_path=_blob_path(SHA_A),
            attempts=[invalid_reference_attempt],
        )
        with self.assertRaises(ValueError):
            build_media_recovery_audit(
                run_id="run-invalid-reference-outcome",
                request_sha256=SHA_A,
                policy_inputs_sha256=None,
                asset_index_sha256=SHA_B,
                records={"binary-outcome": invalid_reference_record},
            )

        for reason in (
            "declared_size_mismatch",
            "mime_mismatch",
            "media_type_unverified",
        ):
            with self.subTest(warning_reason=reason):
                warning_record = _record(
                    "binary-outcome",
                    url=url,
                    status="captured_with_warning",
                    terminal_reason=reason,
                    actual_bytes=1,
                    sha256=SHA_A,
                    blob_path=_blob_path(SHA_A),
                )
                build_media_recovery_audit(
                    run_id=f"run-warning-{reason}",
                    request_sha256=SHA_A,
                    policy_inputs_sha256=None,
                    asset_index_sha256=SHA_B,
                    records={"binary-outcome": warning_record},
                )

    def test_covered_binary_rejects_synchronized_length_and_mime_tampering(
        self,
    ) -> None:
        url = "https://cdn.discordapp.com/covered-metadata"
        invalid_values = (
            ("boolean length", "http_content_length", True),
            ("mismatched length", "http_content_length", 2),
            ("empty MIME", "http_content_type", ""),
            ("uppercase MIME", "http_content_type", "IMAGE/PNG"),
            ("parameterized MIME", "http_content_type", "image/png; charset=utf-8"),
        )
        for label, field, value in invalid_values:
            attempt = _attempt(
                url,
                status="complete",
                terminal_reason="downloaded",
                actual_bytes=1,
                sha256=SHA_A,
                blob_path=_blob_path(SHA_A),
            )
            attempt.update(
                {
                    "http_content_type": "image/png",
                    "http_content_length": 1,
                    field: value,
                }
            )
            record = _record(
                "covered-metadata",
                url=url,
                status="complete",
                terminal_reason="downloaded",
                actual_bytes=1,
                sha256=SHA_A,
                blob_path=_blob_path(SHA_A),
                attempts=[attempt],
            )
            record.update(
                {
                    "http_content_type": "image/png",
                    "http_content_length": 1,
                    field: value,
                }
            )

            with self.subTest(label=label), self.assertRaises(ValueError):
                build_media_recovery_audit(
                    run_id="run-covered-metadata-tamper",
                    request_sha256=SHA_A,
                    policy_inputs_sha256=None,
                    asset_index_sha256=SHA_B,
                    records={"covered-metadata": record},
                )

    def test_legitimate_nonselected_record_states_emit_no_rows(self) -> None:
        records = {
            "disabled": _record(
                "disabled",
                status="not_requested",
                terminal_reason="asset_download_disabled",
            ),
            "changed": _record(
                "changed",
                status="in_progress",
                terminal_reason="candidate_urls_changed",
            ),
        }

        rows = rebuild_media_recovery_rows(records)

        self.assertEqual(rows, [])
        self.assertTrue(all(value == 0 for value in rebuild_media_recovery_counts(rows).values()))

    def test_failed_404_attempt_is_covered_or_current_blocker(self) -> None:
        covered_url = "https://cdn.discordapp.com/missing?sig=synthetic"
        contradictory = _record(
            "covered",
            status="complete",
            terminal_reason="downloaded",
            actual_bytes=5,
            sha256=SHA_A,
            blob_path=_blob_path(SHA_A),
            attempts=[_attempt(covered_url)],
        )
        with self.assertRaises(ValueError):
            build_media_recovery_audit(
                run_id="run-404-invalid",
                request_sha256=SHA_A,
                policy_inputs_sha256=None,
                asset_index_sha256=SHA_B,
                records={"covered": contradictory},
            )

        covered = deepcopy(contradictory)
        covered["attempt_history"].append(  # type: ignore[union-attr]
            _attempt(
                str(covered["url"]),
                status="complete",
                terminal_reason="downloaded",
                actual_bytes=5,
                sha256=SHA_A,
                blob_path=_blob_path(SHA_A),
            )
        )
        blocker = _record(
            "blocker",
            url="https://cdn.discordapp.com/still-missing",
            terminal_reason="download_http_404",
            attempts=[_attempt("https://cdn.discordapp.com/still-missing")],
        )

        audit = build_media_recovery_audit(
            run_id="run-404",
            request_sha256=SHA_A,
            policy_inputs_sha256=None,
            asset_index_sha256=SHA_B,
            records={"covered": covered, "blocker": blocker},
        )
        rows = _rows_by_key(audit["items"])

        self.assertEqual(
            rows[("covered", "attempt", 1)]["disposition"],
            "candidate_failed_record_covered",
        )
        self.assertEqual(
            rows[("blocker", "record", None)]["disposition"],
            "http_compensation_blocker",
        )
        self.assertEqual(audit["counts"]["unresolved_blockers"], 1)

    def test_typed_pending_rejects_matching_nonzero_media_evidence(self) -> None:
        url = "https://cdn.discordapp.com/pending"
        attempt = _attempt(
            url,
            status="in_progress",
            terminal_reason=None,
            retry_trigger=RESOLUTION_RETRY_TRIGGER,
            actual_bytes=7,
        )
        attempt.update(
            {"http_content_type": "image/png", "http_content_length": 7}
        )
        record = _record(
            "pending",
            url=url,
            status="in_progress",
            terminal_reason=None,
            actual_bytes=7,
            attempts=[attempt],
        )
        record.update(
            {"http_content_type": "image/png", "http_content_length": 7}
        )

        with self.assertRaises(ValueError):
            build_media_recovery_audit(
                run_id="run-pending-evidence",
                request_sha256=SHA_A,
                policy_inputs_sha256=None,
                asset_index_sha256=SHA_B,
                records={"pending": record},
            )

    def test_untyped_in_progress_rejects_matching_nonzero_media_evidence(self) -> None:
        url = "https://cdn.discordapp.com/untyped-progress"
        attempt = _attempt(
            url,
            status="in_progress",
            terminal_reason=None,
            actual_bytes=7,
        )
        attempt.update(
            {"http_content_type": "image/png", "http_content_length": 7}
        )
        record = _record(
            "untyped-progress",
            url=url,
            status="in_progress",
            terminal_reason=None,
            actual_bytes=7,
            attempts=[attempt],
        )
        record.update(
            {"http_content_type": "image/png", "http_content_length": 7}
        )

        with self.assertRaises(ValueError):
            build_media_recovery_audit(
                run_id="run-untyped-progress-evidence",
                request_sha256=SHA_A,
                policy_inputs_sha256=None,
                asset_index_sha256=SHA_B,
                records={"untyped-progress": record},
            )

    def test_typed_pending_tail_cannot_be_hidden_by_current_binary(self) -> None:
        url = "https://cdn.discordapp.com/pending-hidden-by-binary"
        first = _attempt(
            url,
            terminal_reason="media_resolution_failed_transient",
            failure_detail="resolver_timeout",
        )
        first.update(
            {"policy_inputs_sha256": None, "resolution_retry_sequence": 1}
        )
        pending = _attempt(
            url,
            status="in_progress",
            terminal_reason=None,
            retry_trigger=RESOLUTION_RETRY_TRIGGER,
        )
        pending.update(
            {
                "retry_of_attempt_number": 1,
                "policy_inputs_sha256": None,
                "resolution_retry_sequence": 2,
            }
        )
        record = _record(
            "pending-hidden-by-binary",
            url=url,
            status="complete",
            terminal_reason="downloaded",
            actual_bytes=1,
            sha256=SHA_A,
            blob_path=_blob_path(SHA_A),
            attempts=[first, pending],
        )

        with self.assertRaises(ValueError):
            build_media_recovery_audit(
                run_id="run-pending-hidden-by-binary",
                request_sha256=SHA_A,
                policy_inputs_sha256=None,
                asset_index_sha256=SHA_B,
                records={"pending-hidden-by-binary": record},
            )

    def test_counts_have_exact_keys_and_enforce_all_partitions(self) -> None:
        records = {
            "pending": _record(
                "pending", terminal_reason="media_resolution_failed_transient"
            ),
            "exhausted": _record(
                "exhausted", terminal_reason="media_resolution_retry_exhausted"
            ),
            "unresolved": _record(
                "unresolved", terminal_reason="media_resolution_unresolved"
            ),
            "invalid": _record(
                "invalid", terminal_reason="media_resolution_invalid_answer"
            ),
            "unsafe": _record("unsafe", terminal_reason="unsafe_media_url"),
            "http": _record(
                "http",
                terminal_reason="download_http_415",
                attempts=[
                    _attempt(
                        "https://cdn.discordapp.com/http",
                        terminal_reason="download_http_415",
                        retry_trigger=LEGACY_RETRY_TRIGGER,
                    )
                ],
            ),
            "hard": _record("hard", terminal_reason="media_type_mismatch"),
            "other": _record("other", terminal_reason="content_length_mismatch"),
            "reference": _record(
                "reference",
                status="reference_only",
                terminal_reason="media_reference_not_binary",
            ),
        }

        rows = rebuild_media_recovery_rows(records)
        counts = rebuild_media_recovery_counts(rows)

        self.assertEqual(set(counts), COUNT_KEYS)
        self.assertEqual(counts["rows_total"], counts["attempt_rows"] + counts["record_rows"])
        self.assertEqual(
            counts["record_rows"],
            counts["current_failed_records"]
            + counts["current_reference_only_records"],
        )
        self.assertEqual(counts["unresolved_blockers"], counts["current_failed_records"])
        self.assertEqual(counts["current_failed_records"], 8)
        self.assertEqual(counts["current_reference_only_records"], 1)
        self.assertEqual(
            sum(
                counts[key]
                for key in [
                    "resolution_retry_pending_records",
                    "resolution_retry_exhausted_records",
                    "resolution_unresolved_records",
                    "resolution_invalid_answer_records",
                    "unsafe_records",
                    "http_compensation_records",
                    "hard_media_failure_records",
                    "other_media_failure_records",
                ]
            ),
            counts["current_failed_records"],
        )
        self.assertEqual(counts["legacy_attempt_rows"], 1)
        self.assertEqual(counts["typed_resolution_attempt_rows"], 5)
        self.assertEqual(counts["http_400_404_415_attempt_rows"], 1)

    def test_top_level_artifact_has_only_fixed_fields_and_stable_bytes(self) -> None:
        record = _record("artifact")

        first = build_media_recovery_audit(
            run_id="run-artifact",
            request_sha256=SHA_A,
            policy_inputs_sha256=None,
            asset_index_sha256=SHA_B,
            records={"artifact": record},
        )
        second = build_media_recovery_audit(
            run_id="run-artifact",
            request_sha256=SHA_A,
            policy_inputs_sha256=None,
            asset_index_sha256=SHA_B,
            records={"artifact": deepcopy(record)},
        )

        self.assertEqual(
            set(first),
            {
                "version",
                "kind",
                "run_id",
                "request_sha256",
                "policy_inputs_sha256",
                "asset_index_sha256",
                "counts",
                "items",
            },
        )
        self.assertEqual(first["version"], 2)
        self.assertEqual(first["kind"], MEDIA_RECOVERY_AUDIT_KIND)
        self.assertEqual(
            canonical_media_recovery_audit_bytes(first),
            canonical_media_recovery_audit_bytes(second),
        )
        json.loads(canonical_media_recovery_audit_bytes(first))

    def test_malformed_identities_records_attempts_and_evidence_fail_closed(self) -> None:
        valid = _record("valid")
        invalid_cases: list[object] = [
            [],
            {"valid": None},
            {"valid": {**valid, "logical_key": "different"}},
            {"valid": {**valid, "logical_key": ""}},
            {"valid": {**valid, "url": 7}},
            {"valid": {**valid, "attempt_history": {}}},
            {"valid": {**valid, "attempt_history": [None]}},
            {"valid": {**valid, "status": "mystery"}},
            {"valid": {**valid, "status": []}},
            {"valid": {**valid, "actual_bytes": True}},
            {"valid": {**valid, "actual_bytes": -1}},
            {"valid": {**valid, "sha256": SHA_A, "blob_path": None}},
            {"valid": {**valid, "url": "https://cdn.discordapp.com/\ud800"}},
            {
                "key-\ud800": _record(
                    "key-\ud800",
                    status="complete",
                    terminal_reason="downloaded",
                    actual_bytes=1,
                    sha256=SHA_A,
                    blob_path=_blob_path(SHA_A),
                )
            },
            {
                "valid": {
                    **valid,
                    "attempt_history": [
                        _attempt(
                            "https://cdn.discordapp.com/invalid-detail",
                            failure_detail=7,
                        )
                    ],
                }
            },
            {
                "valid": {
                    **valid,
                    "attempt_history": [
                        {**_attempt("https://cdn.discordapp.com/status"), "status": {}}
                    ],
                }
            },
            {
                "valid": {
                    **valid,
                    "attempt_history": [
                        {
                            **_attempt("https://cdn.discordapp.com/trigger"),
                            "retry_trigger": [],
                        }
                    ],
                }
            },
            {
                "valid": {
                    **valid,
                    "attempt_history": [
                        _attempt(
                            "https://cdn.discordapp.com/detail",
                            terminal_reason="media_resolution_failed_transient",
                            failure_detail=[],  # type: ignore[arg-type]
                        )
                    ],
                }
            },
            {
                "valid": {
                    **_record(
                        "valid",
                        terminal_reason="media_resolution_failed_transient",
                    ),
                    "failure_detail": {},
                }
            },
        ]
        for records in invalid_cases:
            with self.subTest(records=records):
                with self.assertRaises(ValueError):
                    rebuild_media_recovery_rows(records)  # type: ignore[arg-type]

    def test_malformed_build_identity_fails_closed(self) -> None:
        valid = {
            "run_id": "run",
            "request_sha256": SHA_A,
            "policy_inputs_sha256": None,
            "asset_index_sha256": SHA_B,
            "records": {},
        }
        changes = [
            {"run_id": ""},
            {"run_id": 1},
            {"run_id": "run-\ud800"},
            {"request_sha256": "A" * 64},
            {"request_sha256": "short"},
            {"policy_inputs_sha256": "short"},
            {"asset_index_sha256": "g" * 64},
        ]
        for change in changes:
            arguments = {**valid, **change}
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    build_media_recovery_audit(**arguments)  # type: ignore[arg-type]

    def test_malformed_rows_duplicate_ids_and_count_partitions_fail_closed(self) -> None:
        rows = rebuild_media_recovery_rows({"valid": _record("valid")})
        attempt_rows = rebuild_media_recovery_rows(
            {
                "attempt": _record(
                    "attempt",
                    attempts=[
                        _attempt(
                            "https://cdn.discordapp.com/attempt",
                            retry_trigger=RESOLUTION_RETRY_TRIGGER,
                        )
                    ],
                )
            }
        )
        attempt_row = next(
            row for row in attempt_rows if row["item_kind"] == "attempt"
        )
        malformed_rows = [
            [{}],
            [*rows, deepcopy(rows[0])],
            [{**rows[0], "item_kind": "attempt"}],
            [{**rows[0], "item_kind": []}],
            [{**rows[0], "row_id": "0" * 64}],
            [{**rows[0], "disposition": "reference_only_not_binary"}],
            [{**rows[0], "status": "reference_only"}],
            [{**rows[0], "binary_captured": True}],
            [{**rows[0], "candidate_host": "sig=synthetic"}],
            [{**attempt_row, "retry_trigger": []}],
            [{**attempt_row, "failure_detail": {}}],
        ]
        for candidate in malformed_rows:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    rebuild_media_recovery_counts(candidate)


if __name__ == "__main__":
    unittest.main()
