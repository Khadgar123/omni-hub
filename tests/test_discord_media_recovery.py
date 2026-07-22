from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest

import omni_hub.discord_media_recovery as media_recovery_module

from omni_hub.connectors.discord import (
    rfc2544_fake_ip_media_policy_descriptor,
)
from omni_hub.discord_media_recovery import (
    FRESH_SECURITY_REJECTION_PROVENANCE,
    LEGACY_RETRY_TRIGGER,
    MAX_RESOLUTION_RETRY_SEQUENCES,
    RESOLUTION_RETRY_TRIGGER,
    MediaResolutionContext,
    discord_media_candidate_observation_metadata,
    discord_media_identity_metadata,
    discord_media_reference_candidate_ledger_is_exact,
    is_discord_external_proxy_url,
    legacy_recovery_retry_of,
    media_resolution_context,
    next_resolution_retry_metadata,
    normalized_discord_media_mime,
    reusable_resolution_attempt_number,
    validate_media_record_attempt_consistency,
    validate_resolution_attempt_history,
)


REQUEST_SHA256 = "a" * 64
CANDIDATE_URL = (
    "https://media.discordapp.net/external/item?sig=synthetic"
)
DIRECT_URL = "https://cdn.discordapp.com/attachments/item?sig=synthetic"
OTHER_URL = "https://images-ext-1.discordapp.net/external/other?sig=synthetic"


def _request_identity(*, opt_in: bool = True) -> dict[str, object]:
    options: dict[str, object] = {
        "max_pages": None,
        "download_assets": True,
        "max_asset_bytes": 1024,
    }
    if opt_in:
        options.update(
            {
                "allow_rfc2544_fake_ip": True,
                "rfc2544_fake_ip_policy": (
                    rfc2544_fake_ip_media_policy_descriptor()
                ),
            }
        )
    return {"options": options}


def _context(*, opt_in: bool = True) -> MediaResolutionContext:
    return media_resolution_context(
        _request_identity(opt_in=opt_in),
        REQUEST_SHA256,
    )


def _attempt(
    *,
    url: str = CANDIDATE_URL,
    status: str = "failed",
    terminal_reason: str | None = "unsafe_media_url",
    actual_bytes: int = 0,
) -> dict[str, object]:
    return {
        "url": url,
        "status": status,
        "terminal_reason": terminal_reason,
        "http_content_type": None,
        "http_content_length": None,
        "actual_bytes": actual_bytes,
        "sha256": None,
        "blob_path": None,
    }


def _legacy_record(
    *,
    candidate_url: str = CANDIDATE_URL,
    proxy_url: str | None = CANDIDATE_URL,
) -> dict[str, object]:
    source = {
        "message_id": "1",
        "channel_id": "2",
        "stream": "messages_2",
    }
    metadata = {
        "id": "item",
        "size": 1,
        "content_type": None,
        "proxy_url": proxy_url,
    }
    return {
        "schema_version": 3,
        "logical_key": "synthetic:attachment:item",
        "kind": "attachment",
        "field": "attachment",
        "url": candidate_url,
        "candidate_urls": [candidate_url],
        "declared_metadata": metadata,
        "declared_content_type": None,
        "identity_metadata": {
            "id": "item",
            "size": 1,
            "content_type": None,
        },
        "identity_conflicts": [],
        "sources": [source],
        "observations": [
            {
                "source": deepcopy(source),
                "url": DIRECT_URL if proxy_url is not None else candidate_url,
                "proxy_url": proxy_url,
                "metadata": deepcopy(metadata),
            }
        ],
        "status": "failed",
        "terminal_reason": "unsafe_media_url",
        "http_content_type": None,
        "http_content_length": None,
        "actual_bytes": 0,
        "sha256": None,
        "blob_path": None,
        "attempt_history": [_attempt(url=candidate_url)],
    }


def _typed_attempt(
    *,
    sequence: int,
    context: MediaResolutionContext,
    url: str = CANDIDATE_URL,
    status: str = "failed",
    terminal_reason: str | None = "media_resolution_failed_transient",
    failure_detail: str | None = "resolver_eai_again",
    retry_trigger: str | None = None,
    retry_of_attempt_number: int | None = None,
) -> dict[str, object]:
    attempt = _attempt(
        url=url,
        status=status,
        terminal_reason=terminal_reason,
    )
    attempt.update(
        {
            "policy_inputs_sha256": context.policy_inputs_sha256,
            "resolution_retry_sequence": sequence,
        }
    )
    if failure_detail is not None:
        attempt["failure_detail"] = failure_detail
    if retry_trigger is not None:
        attempt["retry_trigger"] = retry_trigger
    if retry_of_attempt_number is not None:
        attempt["retry_of_attempt_number"] = retry_of_attempt_number
    return attempt


def _binary_attempt(
    *,
    url: str = OTHER_URL,
    status: str = "complete",
) -> dict[str, object]:
    attempt = _attempt(
        url=url,
        status=status,
        terminal_reason="downloaded",
        actual_bytes=1,
    )
    attempt.update(
        {
            "http_content_type": "application/octet-stream",
            "http_content_length": 1,
            "sha256": "b" * 64,
            "blob_path": "assets/blob.bin",
        }
    )
    return attempt


def _bind_producer_metadata(
    record: dict[str, object],
    *,
    kind: str,
    field: str,
    metadata: dict[str, object],
) -> None:
    sources = record["sources"]
    assert isinstance(sources, list) and sources
    record["kind"] = kind
    record["field"] = field
    record["declared_metadata"] = deepcopy(metadata)
    record["declared_content_type"] = (
        None
        if kind == "sticker"
        else normalized_discord_media_mime(metadata.get("content_type"))
    )
    record["identity_metadata"] = discord_media_identity_metadata(
        kind,
        metadata,
    )
    attempt_history = record.get("attempt_history", [])
    assert isinstance(attempt_history, list)
    candidate_urls: list[object] = []
    for attempt in attempt_history:
        assert isinstance(attempt, dict)
        if attempt.get("url") not in candidate_urls:
            candidate_urls.append(attempt.get("url"))
    if record["url"] not in candidate_urls:
        candidate_urls.append(record["url"])
    assert all(isinstance(value, str) and value for value in candidate_urls)
    record["candidate_urls"] = candidate_urls
    record["observations"] = [
        {
            "source": deepcopy(sources[0]),
            "url": candidate_url,
            "proxy_url": metadata.get("proxy_url"),
            "metadata": deepcopy(metadata),
        }
        for candidate_url in candidate_urls
    ]


def _sync_observation_metadata(record: dict[str, object]) -> None:
    metadata = record["declared_metadata"]
    observations = record["observations"]
    sources = record["sources"]
    assert isinstance(metadata, dict)
    assert isinstance(observations, list)
    assert isinstance(sources, list) and sources
    for observation in observations:
        assert isinstance(observation, dict)
        observation.setdefault("source", deepcopy(sources[0]))
        observation["metadata"] = deepcopy(metadata)
    current_url = record["url"]
    candidate_urls = record.setdefault("candidate_urls", [])
    assert isinstance(candidate_urls, list)
    if current_url not in candidate_urls:
        candidate_urls.append(current_url)
    if not any(
        observation.get("url") == current_url
        or observation.get("proxy_url") == current_url
        for observation in observations
    ):
        observations.append(
            {
                "source": deepcopy(sources[0]),
                "url": current_url,
                "proxy_url": None,
                "metadata": deepcopy(metadata),
            }
        )


class MediaResolutionContextTests(unittest.TestCase):
    def test_opt_in_binds_the_complete_current_policy_descriptor(self) -> None:
        descriptor = rfc2544_fake_ip_media_policy_descriptor()

        context = _context()

        self.assertTrue(context.allow_rfc2544_fake_ip)
        self.assertEqual(
            context.policy_inputs_sha256,
            "17b89647c19c760f58058291784f0fa55a6b55f7c91c23db738a4221d704e325",
        )
        self.assertEqual(context.policy_descriptor, descriptor)
        self.assertEqual(context.request_sha256, REQUEST_SHA256)

    def test_non_opt_in_binds_null_policy_fields(self) -> None:
        context = _context(opt_in=False)

        self.assertFalse(context.allow_rfc2544_fake_ip)
        self.assertIsNone(context.policy_inputs_sha256)
        self.assertIsNone(context.policy_descriptor)

    def test_request_policy_contradictions_and_mismatches_are_rejected(self) -> None:
        descriptor = rfc2544_fake_ip_media_policy_descriptor()
        mismatched = deepcopy(descriptor)
        mismatched["port"] = 8443
        cases = {
            "opt-in missing descriptor": {
                "options": {"allow_rfc2544_fake_ip": True}
            },
            "opt-in mismatched descriptor": {
                "options": {
                    "allow_rfc2544_fake_ip": True,
                    "rfc2544_fake_ip_policy": mismatched,
                }
            },
            "non-opt-in descriptor": {
                "options": {
                    "allow_rfc2544_fake_ip": False,
                    "rfc2544_fake_ip_policy": descriptor,
                }
            },
            "implicit non-opt-in descriptor": {
                "options": {"rfc2544_fake_ip_policy": descriptor}
            },
            "non-boolean opt-in": {
                "options": {
                    "allow_rfc2544_fake_ip": 1,
                    "rfc2544_fake_ip_policy": descriptor,
                }
            },
            "missing options": {},
            "non-mapping options": {"options": []},
        }
        for label, identity in cases.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                media_resolution_context(identity, REQUEST_SHA256)

    def test_request_sha256_must_be_a_bound_sha256_value(self) -> None:
        for value in (True, "", "a" * 63, "A" * 64, 7):
            with self.subTest(value=value), self.assertRaises(ValueError):
                media_resolution_context(_request_identity(), value)  # type: ignore[arg-type]

    def test_context_construction_does_not_mutate_request_identity(self) -> None:
        identity = _request_identity()
        before = deepcopy(identity)

        media_resolution_context(identity, REQUEST_SHA256)

        self.assertEqual(identity, before)


class LegacyMediaRecoveryEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()

    def test_eligible_attempt_returns_exact_one_based_attempt_number(self) -> None:
        record = _legacy_record()
        before = deepcopy(record)

        retry_of = legacy_recovery_retry_of(
            record,
            "https://media.discordapp.net/external/item?sig=synthetic",
            context=self.context,
        )

        self.assertEqual(retry_of, 1)
        self.assertEqual(
            record["attempt_history"][0]["url"],  # type: ignore[index]
            CANDIDATE_URL,
        )
        self.assertEqual(record, before)

    def test_every_record_and_attempt_evidence_condition_is_required(self) -> None:
        def current_not_unsafe(record: dict[str, object]) -> None:
            record["status"] = "reference_only"
            record["terminal_reason"] = "media_reference_not_binary"

        def current_other_reason(record: dict[str, object]) -> None:
            record["terminal_reason"] = "http_404"

        def latest_candidate_not_unsafe(record: dict[str, object]) -> None:
            record["attempt_history"].append(  # type: ignore[union-attr]
                _attempt(terminal_reason="http_404")
            )

        def record_bytes(record: dict[str, object]) -> None:
            record["actual_bytes"] = 1

        def attempt_bytes(record: dict[str, object]) -> None:
            record["attempt_history"][0]["actual_bytes"] = 1  # type: ignore[index]

        def record_content_type(record: dict[str, object]) -> None:
            record["http_content_type"] = "application/octet-stream"

        def attempt_content_length(record: dict[str, object]) -> None:
            record["attempt_history"][0]["http_content_length"] = 1  # type: ignore[index]

        def record_digest(record: dict[str, object]) -> None:
            record["sha256"] = "b" * 64

        def attempt_blob(record: dict[str, object]) -> None:
            record["attempt_history"][0]["blob_path"] = "assets/blob.bin"  # type: ignore[index]

        def covered_binary_history(record: dict[str, object]) -> None:
            covered = _attempt(
                url=OTHER_URL,
                status="captured_with_warning",
                terminal_reason="mime_mismatch",
                actual_bytes=1,
            )
            covered.update(
                {
                    "http_content_type": "application/octet-stream",
                    "http_content_length": 1,
                    "sha256": "b" * 64,
                    "blob_path": "assets/blob.bin",
                }
            )
            record["attempt_history"].insert(0, covered)  # type: ignore[union-attr]

        cases = {
            "current status": current_not_unsafe,
            "current reason": current_other_reason,
            "latest exact candidate": latest_candidate_not_unsafe,
            "record bytes": record_bytes,
            "attempt bytes": attempt_bytes,
            "record metadata": record_content_type,
            "attempt metadata": attempt_content_length,
            "record digest": record_digest,
            "attempt blob": attempt_blob,
            "covered binary history": covered_binary_history,
        }
        for label, mutate in cases.items():
            record = _legacy_record()
            mutate(record)
            with self.subTest(label=label):
                try:
                    retry_of = legacy_recovery_retry_of(
                        record,
                        CANDIDATE_URL,
                        context=self.context,
                    )
                except ValueError:
                    continue
                self.assertIsNone(retry_of)

    def test_false_is_not_accepted_as_a_zero_byte_integer(self) -> None:
        record = _legacy_record()
        record["actual_bytes"] = False
        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                CANDIDATE_URL,
                context=self.context,
            )
        )

        record = _legacy_record()
        record["attempt_history"][0]["actual_bytes"] = False  # type: ignore[index]
        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                CANDIDATE_URL,
                context=self.context,
            )
        )

    def test_candidate_url_security_conditions_are_all_required(self) -> None:
        candidates = {
            "credentials": "https://user@media.discordapp.net/external/item",
            "non-https": "http://media.discordapp.net/external/item",
            "non-443": "https://media.discordapp.net:444/external/item",
            "zero port": "https://media.discordapp.net:0/external/item",
            "lookalike": "https://media.discordapp.net.example/external/item",
            "unlisted": "https://cdn.discord.com/external/item",
        }
        for label, candidate in candidates.items():
            record = _legacy_record(candidate_url=candidate, proxy_url=None)
            with self.subTest(label=label):
                self.assertIsNone(
                    legacy_recovery_retry_of(
                        record,
                        candidate,
                        context=self.context,
                    )
                )

    def test_explicit_443_and_canonical_official_host_are_eligible(self) -> None:
        candidate = "https://MEDIA.DISCORDAPP.NET.:443/external/item?sig=synthetic"
        record = _legacy_record(candidate_url=candidate, proxy_url=None)

        self.assertEqual(
            legacy_recovery_retry_of(
                record,
                candidate,
                context=self.context,
            ),
            1,
        )

    def test_wrong_request_policy_never_gets_legacy_override(self) -> None:
        descriptor = rfc2544_fake_ip_media_policy_descriptor()
        contexts = (
            _context(opt_in=False),
            replace(self.context, policy_inputs_sha256="b" * 64),
            replace(self.context, policy_descriptor={**descriptor, "port": 8443}),
            replace(self.context, allow_rfc2544_fake_ip=False),
        )
        for context in contexts:
            with self.subTest(context=context):
                self.assertIsNone(
                    legacy_recovery_retry_of(
                        _legacy_record(),
                        CANDIDATE_URL,
                        context=context,
                    )
                )

    def test_existing_legacy_marker_prevents_duplicate_override(self) -> None:
        record = _legacy_record()
        marker = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
            retry_trigger=LEGACY_RETRY_TRIGGER,
            retry_of_attempt_number=1,
        )
        record["attempt_history"].append(marker)  # type: ignore[union-attr]
        record.update(
            {
                field: marker[field]
                for field in (
                    "url",
                    "status",
                    "terminal_reason",
                    "http_content_type",
                    "http_content_length",
                    "actual_bytes",
                    "sha256",
                    "blob_path",
                )
            }
        )

        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                CANDIDATE_URL,
                context=self.context,
            )
        )

    def test_fresh_security_provenance_is_never_legacy_eligible(self) -> None:
        record = _legacy_record()
        record["attempt_history"][0]["security_rejection"] = {  # type: ignore[index]
            "version": 1,
            "reason_code": "media_security_policy_rejected",
            "legacy_eligible": False,
        }

        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                CANDIDATE_URL,
                context=self.context,
            )
        )

    def test_global_fresh_security_tail_blocks_older_candidate_override(self) -> None:
        fresh_url = (
            "https://cdn.discordapp.com/attachments/fresh?sig=synthetic"
        )
        record = _legacy_record(candidate_url=fresh_url, proxy_url=None)
        legacy_attempt = _attempt(url=DIRECT_URL)
        fresh_attempt = _attempt(url=fresh_url)
        fresh_attempt["security_rejection"] = dict(
            FRESH_SECURITY_REJECTION_PROVENANCE
        )
        record.update(
            {
                "candidate_urls": [DIRECT_URL, fresh_url],
                "observations": [
                    {"url": DIRECT_URL, "proxy_url": None},
                    {"url": fresh_url, "proxy_url": None},
                ],
                "attempt_history": [legacy_attempt, fresh_attempt],
            }
        )
        _sync_observation_metadata(record)

        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                DIRECT_URL,
                context=self.context,
            )
        )
        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                fresh_url,
                context=self.context,
            )
        )

    def test_only_current_global_legacy_tail_is_eligible_across_candidates(
        self,
    ) -> None:
        tail_url = (
            "https://cdn.discordapp.com/attachments/tail?sig=synthetic"
        )
        record = _legacy_record(candidate_url=tail_url, proxy_url=None)
        record.update(
            {
                "candidate_urls": [DIRECT_URL, tail_url],
                "observations": [
                    {"url": DIRECT_URL, "proxy_url": None},
                    {"url": tail_url, "proxy_url": None},
                ],
                "attempt_history": [
                    _attempt(url=DIRECT_URL),
                    _attempt(url=tail_url),
                ],
            }
        )
        _sync_observation_metadata(record)

        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                DIRECT_URL,
                context=self.context,
            )
        )
        self.assertEqual(
            legacy_recovery_retry_of(
                record,
                tail_url,
                context=self.context,
            ),
            2,
        )

    def test_external_direct_candidate_is_blocked_when_proxy_is_observed(self) -> None:
        record = _legacy_record(candidate_url=DIRECT_URL, proxy_url=CANDIDATE_URL)

        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                DIRECT_URL,
                context=self.context,
            )
        )

    def test_retained_observation_blocks_direct_candidate_after_metadata_changes(self) -> None:
        for label, current_proxy in (
            ("metadata omitted", None),
            ("metadata changed", OTHER_URL),
        ):
            record = _legacy_record(
                candidate_url=DIRECT_URL,
                proxy_url=current_proxy,
            )
            record["observations"] = [
                {
                    "url": OTHER_URL,
                    "proxy_url": None,
                },
                {
                    "url": DIRECT_URL,
                    "proxy_url": CANDIDATE_URL,
                }
            ]
            _sync_observation_metadata(record)
            with self.subTest(label=label):
                self.assertIsNone(
                    legacy_recovery_retry_of(
                        record,
                        DIRECT_URL,
                        context=self.context,
                    )
                )

    def test_proxy_candidate_requires_current_proxy_metadata_even_if_observed(self) -> None:
        record = _legacy_record(candidate_url=CANDIDATE_URL, proxy_url=None)
        record["observations"] = [
            {
                "url": OTHER_URL,
                "proxy_url": None,
            },
            {
                "url": DIRECT_URL,
                "proxy_url": CANDIDATE_URL,
            }
        ]
        _sync_observation_metadata(record)

        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                CANDIDATE_URL,
                context=self.context,
            )
        )

    def test_proxy_candidate_requires_retained_observation_even_with_current_metadata(self) -> None:
        record = _legacy_record()
        record["observations"] = [
            {
                "url": OTHER_URL,
                "proxy_url": None,
            },
            {
                "url": DIRECT_URL,
                "proxy_url": None,
            }
        ]
        _sync_observation_metadata(record)

        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                CANDIDATE_URL,
                context=self.context,
            )
        )

    def test_standalone_official_cdn_candidate_remains_eligible(self) -> None:
        record = _legacy_record(candidate_url=DIRECT_URL, proxy_url=None)

        self.assertEqual(
            legacy_recovery_retry_of(
                record,
                DIRECT_URL,
                context=self.context,
            ),
            1,
        )

    def test_proxy_candidate_requires_its_own_unsafe_attempt(self) -> None:
        record = _legacy_record(candidate_url=DIRECT_URL, proxy_url=CANDIDATE_URL)

        self.assertIsNone(
            legacy_recovery_retry_of(
                record,
                CANDIDATE_URL,
                context=self.context,
            )
        )

    def test_external_proxy_url_recognizer_matches_existing_narrow_contract(self) -> None:
        accepted = (
            CANDIDATE_URL,
            "https://images-ext-27.discordapp.net/external/item",
        )
        rejected = (
            None,
            7,
            "http://media.discordapp.net/external/item",
            "https://user@media.discordapp.net/external/item",
            "https://media.discordapp.net:443/external/item",
            "https://media.discordapp.net/attachments/item",
            "https://media.discordapp.net.example/external/item",
        )
        for value in accepted:
            with self.subTest(value=value):
                self.assertTrue(is_discord_external_proxy_url(value))
        for value in rejected:
            with self.subTest(value=value):
                self.assertFalse(is_discord_external_proxy_url(value))


class MediaResolutionHistoryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()

    def _record(self, attempts: list[dict[str, object]]) -> dict[str, object]:
        record = _legacy_record()
        record["attempt_history"] = attempts
        if attempts:
            latest = attempts[-1]
            record.update(
                {
                    "url": latest["url"],
                    "status": (
                        "in_progress"
                        if latest["status"] == "interrupted"
                        else latest["status"]
                    ),
                    "terminal_reason": latest["terminal_reason"],
                    "http_content_type": latest["http_content_type"],
                    "http_content_length": latest["http_content_length"],
                    "actual_bytes": latest["actual_bytes"],
                    "sha256": latest["sha256"],
                    "blob_path": latest["blob_path"],
                }
            )
        _sync_observation_metadata(record)
        return record

    def test_typed_pending_must_be_global_tail_and_mirror_current_record(self) -> None:
        pending = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
        )
        mismatched_current = self._record([pending])
        mismatched_current.update(
            {"status": "failed", "terminal_reason": "unsafe_media_url"}
        )
        pending_before_later_attempt = self._record(
            [
                pending,
                _attempt(url=OTHER_URL, terminal_reason="download_http_404"),
            ]
        )

        for label, record in (
            ("current mismatch", mismatched_current),
            ("not global tail", pending_before_later_attempt),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_resolution_attempt_history(record, context=self.context)
            with self.subTest(label=f"{label} reusable"):
                self.assertIsNone(
                    reusable_resolution_attempt_number(record, CANDIDATE_URL)
                )

    def test_typed_pending_tail_cannot_be_hidden_by_current_binary(self) -> None:
        first = _typed_attempt(sequence=1, context=self.context)
        pending = _typed_attempt(
            sequence=2,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
            retry_trigger=RESOLUTION_RETRY_TRIGGER,
            retry_of_attempt_number=1,
        )
        forged_binary = self._record([first, pending])
        successful = _binary_attempt(url=CANDIDATE_URL)
        forged_binary.update(
            {
                field: successful[field]
                for field in (
                    "url",
                    "status",
                    "terminal_reason",
                    "http_content_type",
                    "http_content_length",
                    "actual_bytes",
                    "sha256",
                    "blob_path",
                )
            }
        )

        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(forged_binary)

    def test_typed_pending_rejects_matching_nonzero_media_evidence(self) -> None:
        pending = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
        )
        pending.update(
            {
                "http_content_type": "image/png",
                "http_content_length": 7,
                "actual_bytes": 7,
            }
        )
        record = self._record([pending])

        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(record)

    def test_pending_current_state_requires_null_failure_detail(self) -> None:
        pending = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
        )
        record = self._record([pending])
        record["failure_detail"] = "stale-diagnostic"

        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(record)

    def test_untyped_interrupted_download_may_retain_partial_evidence(self) -> None:
        interrupted = _attempt(
            status="interrupted",
            terminal_reason="interrupted",
            actual_bytes=7,
        )
        interrupted.update(
            {
                "http_content_type": "image/png",
                "http_content_length": 11,
            }
        )

        validate_media_record_attempt_consistency(self._record([interrupted]))

    def test_in_progress_state_requires_zero_media_evidence(self) -> None:
        in_progress = _attempt(
            status="in_progress",
            terminal_reason=None,
            actual_bytes=7,
        )
        in_progress.update(
            {
                "http_content_type": "image/png",
                "http_content_length": 7,
            }
        )
        matching_history = self._record([in_progress])
        empty_history = deepcopy(matching_history)
        empty_history["attempt_history"] = []
        changed_candidates = deepcopy(empty_history)
        changed_candidates["terminal_reason"] = "candidate_urls_changed"

        for label, record in (
            ("matching untyped attempt", matching_history),
            ("empty history", empty_history),
            ("candidate URLs changed", changed_candidates),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(record)

    def test_in_progress_record_without_history_remains_valid(self) -> None:
        record = _legacy_record()
        record.update(
            {
                "status": "in_progress",
                "terminal_reason": None,
                "attempt_history": [],
            }
        )

        validate_media_record_attempt_consistency(record)

    def test_current_interrupted_cannot_bypass_old_failed_attempt(self) -> None:
        record = self._record([_attempt()])
        record.update(
            {
                "status": "in_progress",
                "terminal_reason": "interrupted",
                "actual_bytes": 0,
            }
        )

        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(record, context=self.context)

    def test_recovery_history_requires_matching_later_binary_success(self) -> None:
        failed_404 = _attempt(terminal_reason="download_http_404")
        successful = _binary_attempt(url=CANDIDATE_URL)
        covered_without_success = self._record([failed_404])
        covered_without_success.update(
            {
                "url": successful["url"],
                "status": successful["status"],
                "terminal_reason": successful["terminal_reason"],
                "http_content_type": successful["http_content_type"],
                "http_content_length": successful["http_content_length"],
                "actual_bytes": successful["actual_bytes"],
                "sha256": successful["sha256"],
                "blob_path": successful["blob_path"],
            }
        )

        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                covered_without_success,
                context=self.context,
            )

        validate_resolution_attempt_history(
            self._record([failed_404, successful]),
            context=self.context,
        )
        validate_resolution_attempt_history(
            self._record([successful]),
            context=self.context,
        )

    def test_unsafe_history_requires_matching_later_binary_success(self) -> None:
        successful = _binary_attempt(url=CANDIDATE_URL)
        for label, marked in (("legacy", False), ("fresh", True)):
            unsafe = _attempt()
            if marked:
                unsafe["security_rejection"] = dict(
                    FRESH_SECURITY_REJECTION_PROVENANCE
                )
            forged_complete = self._record([unsafe])
            forged_complete.update(
                {
                    field: successful[field]
                    for field in (
                        "url",
                        "status",
                        "terminal_reason",
                        "http_content_type",
                        "http_content_length",
                        "actual_bytes",
                        "sha256",
                        "blob_path",
                    )
                }
            )

            with self.subTest(label=f"{label} forged"), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(forged_complete)
            if marked:
                with self.subTest(label=f"{label} covered"), self.assertRaises(
                    ValueError
                ):
                    validate_media_record_attempt_consistency(
                        self._record([unsafe, successful])
                    )
            else:
                with self.subTest(label=f"{label} covered"):
                    validate_media_record_attempt_consistency(
                        self._record([unsafe, successful])
                    )

    def test_covered_outcome_reason_and_failure_detail_are_exact(self) -> None:
        invalid_reason = _binary_attempt(url=CANDIDATE_URL)
        invalid_reason["terminal_reason"] = "download_http_404"
        stale_detail = _binary_attempt(url=CANDIDATE_URL)
        stale_detail["failure_detail"] = "resolver_timeout"
        invalid_reference = _binary_attempt(
            url=CANDIDATE_URL,
            status="reference_only",
        )
        invalid_reference["terminal_reason"] = "unsafe_media_url"

        stale_detail_record = self._record([stale_detail])
        stale_detail_record["failure_detail"] = "resolver_timeout"
        for label, record in (
            ("complete with HTTP failure", self._record([invalid_reason])),
            ("complete with stale detail", stale_detail_record),
            ("reference with security failure", self._record([invalid_reference])),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(record)

        for reason in (
            "declared_size_mismatch",
            "mime_mismatch",
            "media_type_unverified",
        ):
            with self.subTest(warning_reason=reason):
                warning = _binary_attempt(
                    url=CANDIDATE_URL,
                    status="captured_with_warning",
                )
                warning["terminal_reason"] = reason
                warning_record = self._record([warning])
                if reason == "declared_size_mismatch":
                    _bind_producer_metadata(
                        warning_record,
                        kind="attachment",
                        field="attachment",
                        metadata={
                            "id": "item",
                            "size": 2,
                            "content_type": "application/octet-stream",
                        },
                    )
                elif reason == "mime_mismatch":
                    _bind_producer_metadata(
                        warning_record,
                        kind="attachment",
                        field="attachment",
                        metadata={
                            "id": "item",
                            "size": 1,
                            "content_type": "application/json",
                        },
                    )
                else:
                    warning["http_content_type"] = None
                    warning_record["http_content_type"] = None
                    _bind_producer_metadata(
                        warning_record,
                        kind="embed",
                        field="video",
                        metadata={"url": CANDIDATE_URL, "proxy_url": None},
                    )
                validate_media_record_attempt_consistency(warning_record)

        reference = _binary_attempt(
            url=CANDIDATE_URL,
            status="reference_only",
        )
        reference["terminal_reason"] = "media_reference_not_binary"
        reference_record = self._record([reference])
        _bind_producer_metadata(
            reference_record,
            kind="embed",
            field="video",
            metadata={"url": CANDIDATE_URL, "proxy_url": None},
        )
        validate_media_record_attempt_consistency(reference_record)

    def test_covered_binary_length_and_mime_are_producer_normalized(self) -> None:
        invalid_values = (
            ("boolean length", "http_content_length", True),
            ("negative length", "http_content_length", -1),
            ("mismatched length", "http_content_length", 2),
            ("empty MIME", "http_content_type", ""),
            ("uppercase MIME", "http_content_type", "IMAGE/PNG"),
            ("padded MIME", "http_content_type", " image/png "),
            ("parameterized MIME", "http_content_type", "image/png; charset=utf-8"),
        )
        for label, field, value in invalid_values:
            attempt = _binary_attempt(url=CANDIDATE_URL)
            attempt[field] = value
            record = self._record([attempt])
            record[field] = value
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(record)

        for content_type, content_length in (
            (None, None),
            ("image/png", 1),
        ):
            attempt = _binary_attempt(url=CANDIDATE_URL)
            attempt["http_content_type"] = content_type
            attempt["http_content_length"] = content_length
            validate_media_record_attempt_consistency(self._record([attempt]))

    def test_complete_and_warning_binary_outcomes_require_nonzero_body(self) -> None:
        cases = (
            ("complete", "downloaded", 0),
            ("captured_with_warning", "declared_size_mismatch", 1),
        )
        for status, terminal_reason, declared_size in cases:
            attempt = _binary_attempt(url=CANDIDATE_URL, status=status)
            attempt.update(
                {
                    "terminal_reason": terminal_reason,
                    "actual_bytes": 0,
                    "http_content_length": 0,
                }
            )
            record = self._record([attempt])
            _bind_producer_metadata(
                record,
                kind="attachment",
                field="attachment",
                metadata={
                    "id": "item",
                    "size": declared_size,
                    "content_type": "application/octet-stream",
                },
            )

            with self.subTest(status=status), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(record)

    def test_every_historical_covered_attempt_requires_blob_identity(self) -> None:
        cases = (
            (
                "complete",
                "downloaded",
                "application/octet-stream",
                "attachment",
                "attachment",
                {"id": "item", "size": 1, "content_type": "application/octet-stream"},
            ),
            (
                "captured_with_warning",
                "declared_size_mismatch",
                "application/octet-stream",
                "attachment",
                "attachment",
                {"id": "item", "size": 2, "content_type": "application/octet-stream"},
            ),
            (
                "reference_only",
                "media_reference_not_binary",
                "text/html",
                "embed",
                "video",
                {"url": CANDIDATE_URL, "proxy_url": None},
            ),
        )
        for status, reason, content_type, kind, field, metadata in cases:
            attempt = _binary_attempt(url=CANDIDATE_URL, status=status)
            attempt["terminal_reason"] = reason
            attempt["http_content_type"] = content_type
            record = self._record([attempt])
            _bind_producer_metadata(
                record,
                kind=kind,
                field=field,
                metadata=metadata,
            )
            validate_media_record_attempt_consistency(record)

            tampered = deepcopy(record)
            tampered["attempt_history"][0]["sha256"] = None  # type: ignore[index]
            tampered["attempt_history"][0]["blob_path"] = None  # type: ignore[index]
            with self.subTest(status=status), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(tampered)

    def test_warning_and_reference_are_terminal_for_the_same_candidate(self) -> None:
        warning = _binary_attempt(
            url=CANDIDATE_URL,
            status="captured_with_warning",
        )
        warning["terminal_reason"] = "declared_size_mismatch"
        warning_record = self._record([warning])
        _bind_producer_metadata(
            warning_record,
            kind="attachment",
            field="attachment",
            metadata={
                "id": "item",
                "size": 2,
                "content_type": "application/octet-stream",
            },
        )

        reference = _binary_attempt(url=CANDIDATE_URL, status="reference_only")
        reference["terminal_reason"] = "media_reference_not_binary"
        reference["http_content_type"] = "text/html"
        reference_record = self._record([reference])
        _bind_producer_metadata(
            reference_record,
            kind="embed",
            field="video",
            metadata={"url": CANDIDATE_URL, "proxy_url": None},
        )

        for label, base in (("warning", warning_record), ("reference", reference_record)):
            same_url = deepcopy(base)
            same_url["attempt_history"].append(  # type: ignore[union-attr]
                _attempt(url=CANDIDATE_URL, terminal_reason="download_http_404")
            )
            same_url.update(
                {
                    "status": "failed",
                    "terminal_reason": "download_http_404",
                    "http_content_type": None,
                    "http_content_length": None,
                    "actual_bytes": 0,
                    "sha256": None,
                    "blob_path": None,
                }
            )
            with self.subTest(label=f"{label} same URL"), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(same_url)

            other_url = deepcopy(base)
            other_url["attempt_history"].append(  # type: ignore[union-attr]
                _attempt(url=OTHER_URL, terminal_reason="download_http_404")
            )
            other_url.update(
                {
                    "url": OTHER_URL,
                    "status": "failed",
                    "terminal_reason": "download_http_404",
                    "http_content_type": None,
                    "http_content_length": None,
                    "actual_bytes": 0,
                    "sha256": None,
                    "blob_path": None,
                }
            )
            _sync_observation_metadata(other_url)
            with self.subTest(label=f"{label} other URL"):
                validate_media_record_attempt_consistency(other_url)

    def test_covered_outcome_must_equal_recomputed_producer_outcome(self) -> None:
        embed_html = _binary_attempt(url=CANDIDATE_URL)
        embed_html["http_content_type"] = "text/html"
        forged_embed_complete = self._record([embed_html])
        _bind_producer_metadata(
            forged_embed_complete,
            kind="embed",
            field="video",
            metadata={"url": CANDIDATE_URL, "proxy_url": None},
        )

        attachment_html = _binary_attempt(url=CANDIDATE_URL)
        attachment_html["http_content_type"] = "text/html"
        forged_attachment_complete = self._record([attachment_html])
        _bind_producer_metadata(
            forged_attachment_complete,
            kind="attachment",
            field="attachment",
            metadata={"id": "item", "size": 1, "content_type": "image/png"},
        )

        wrong_size = _binary_attempt(url=CANDIDATE_URL)
        wrong_size["http_content_type"] = "image/png"
        forged_size_complete = self._record([wrong_size])
        _bind_producer_metadata(
            forged_size_complete,
            kind="attachment",
            field="attachment",
            metadata={"id": "item", "size": 2, "content_type": "image/png"},
        )

        for label, record in (
            ("embed HTML forged complete", forged_embed_complete),
            ("attachment family mismatch forged complete", forged_attachment_complete),
            ("declared size mismatch forged complete", forged_size_complete),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(record)

        legitimate_warning = deepcopy(forged_size_complete)
        legitimate_warning["status"] = "captured_with_warning"
        legitimate_warning["terminal_reason"] = "declared_size_mismatch"
        legitimate_warning["attempt_history"][-1].update(
            {
                "status": "captured_with_warning",
                "terminal_reason": "declared_size_mismatch",
            }
        )
        validate_media_record_attempt_consistency(legitimate_warning)

    def test_producer_metadata_binds_declared_identity_and_observation(self) -> None:
        attempt = _binary_attempt(url=CANDIDATE_URL)
        record = self._record([attempt])
        _bind_producer_metadata(
            record,
            kind="attachment",
            field="attachment",
            metadata={
                "id": "item",
                "size": 1,
                "content_type": "application/octet-stream",
            },
        )
        validate_media_record_attempt_consistency(record)

        forged_source = deepcopy(record)
        forged_source["observations"][0]["source"] = {
            "message_id": "forged",
            "channel_id": "forged",
            "stream": "forged",
        }
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(forged_source)

        declared_type_tamper = deepcopy(record)
        declared_type_tamper["declared_content_type"] = "text/plain"
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(declared_type_tamper)

        stale_observation = deepcopy(record)
        changed_metadata = {
            "id": "item",
            "size": 1,
            "content_type": "image/png",
        }
        stale_observation["declared_metadata"] = changed_metadata
        stale_observation["declared_content_type"] = "image/png"
        stale_observation["identity_metadata"] = discord_media_identity_metadata(
            "attachment",
            changed_metadata,
        )
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(stale_observation)

    def test_schema3_candidate_ledger_requires_baseline_observation_authority(
        self,
    ) -> None:
        attempt = _binary_attempt(url=CANDIDATE_URL)
        record = self._record([attempt])
        _bind_producer_metadata(
            record,
            kind="attachment",
            field="attachment",
            metadata={
                "id": "item",
                "size": 1,
                "content_type": "application/octet-stream",
            },
        )
        unobserved_url = "https://cdn.discordapp.com/attachments/unobserved"
        record["candidate_urls"] = [CANDIDATE_URL, unobserved_url]
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(record)

        for field_path in (
            ("url",),
            ("proxy_url",),
            ("metadata", "url"),
            ("metadata", "proxy_url"),
        ):
            authorized = deepcopy(record)
            observation = deepcopy(authorized["observations"][0])
            observation["url"] = DIRECT_URL
            observation["proxy_url"] = None
            observation["metadata"].pop("url", None)
            observation["metadata"].pop("proxy_url", None)
            if len(field_path) == 1:
                observation[field_path[0]] = unobserved_url
            else:
                observation[field_path[0]][field_path[1]] = unobserved_url
            authorized["observations"].append(observation)
            with self.subTest(field_path=field_path):
                validate_media_record_attempt_consistency(authorized)

    def test_candidate_observation_binding_uses_exact_producer_authority(
        self,
    ) -> None:
        attempt = _binary_attempt(url=CANDIDATE_URL)
        record = self._record([attempt])
        metadata = {
            "id": "item",
            "filename": "asset.bin",
            "size": 1,
            "content_type": "application/octet-stream",
        }
        _bind_producer_metadata(
            record,
            kind="attachment",
            field="attachment",
            metadata=metadata,
        )
        fresh_url = "https://media.discordapp.net/external/fresh-authority"
        for field_path in (
            ("url",),
            ("proxy_url",),
            ("metadata", "url"),
            ("metadata", "proxy_url"),
        ):
            candidate = deepcopy(record)
            observation = {
                "source": deepcopy(candidate["sources"][0]),
                "url": DIRECT_URL,
                "proxy_url": None,
                "metadata": {**metadata, "binding": "/".join(field_path)},
            }
            if len(field_path) == 1:
                observation[field_path[0]] = fresh_url
            else:
                observation[field_path[0]][field_path[1]] = fresh_url
            candidate["observations"].append(observation)
            with self.subTest(field_path=field_path):
                self.assertEqual(
                    discord_media_candidate_observation_metadata(
                        candidate,
                        fresh_url,
                    ),
                    observation["metadata"],
                )

        latest = deepcopy(record)
        for binding in ("older", "newer"):
            latest["observations"].append(
                {
                    "source": deepcopy(latest["sources"][0]),
                    "url": fresh_url,
                    "proxy_url": None,
                    "metadata": {**metadata, "binding": binding},
                }
            )
        selected = discord_media_candidate_observation_metadata(
            latest,
            fresh_url,
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected["binding"], "newer")

        rejected_cases = {}
        unretained = deepcopy(latest)
        unretained["observations"][-1]["source"] = {
            "message_id": "unretained",
            "channel_id": "unretained",
            "stream": "unretained",
        }
        unretained["observations"] = [unretained["observations"][-1]]
        rejected_cases["unretained source"] = unretained
        identity_conflict = deepcopy(latest)
        identity_conflict["observations"][-1]["metadata"]["size"] = 2
        identity_conflict["observations"] = [
            identity_conflict["observations"][-1]
        ]
        rejected_cases["identity conflict"] = identity_conflict
        attachment_alias = deepcopy(latest)
        attachment_alias["observations"][-1]["metadata"]["attachment_id"] = (
            metadata["id"]
        )
        attachment_alias["observations"] = [
            attachment_alias["observations"][-1]
        ]
        rejected_cases["attachment alias"] = attachment_alias
        for label, candidate in rejected_cases.items():
            with self.subTest(label=label):
                self.assertIsNone(
                    discord_media_candidate_observation_metadata(
                        candidate,
                        fresh_url,
                    )
                )

    def test_reference_candidate_ledger_requires_observed_terminal_extras(
        self,
    ) -> None:
        source_url = "https://www.youtube.com/embed/ExactLedger"
        historical_url = "https://media.discordapp.net/external/historical"
        active_url = "https://media.discordapp.net/external/active"
        source = {
            "message_id": "1",
            "channel_id": "2",
            "stream": "messages_2",
        }
        metadata = {"url": source_url, "proxy_url": None}
        record = {
            "schema_version": 3,
            "kind": "embed",
            "field": "video",
            "url": source_url,
            "candidate_urls": [source_url, active_url],
            "declared_metadata": metadata,
            "declared_content_type": None,
            "identity_metadata": {},
            "sources": [source],
            "observations": [
                {
                    "source": deepcopy(source),
                    "url": source_url,
                    "proxy_url": None,
                    "metadata": deepcopy(metadata),
                },
                {
                    "source": deepcopy(source),
                    "url": historical_url,
                    "proxy_url": None,
                    "metadata": {"url": historical_url, "proxy_url": None},
                },
                {
                    "source": deepcopy(source),
                    "url": active_url,
                    "proxy_url": None,
                    "metadata": {"url": active_url, "proxy_url": None},
                },
            ],
            "attempt_history": [
                _attempt(url=source_url),
                _attempt(
                    url=historical_url,
                    terminal_reason="download_http_404",
                ),
                _attempt(
                    url=active_url,
                    terminal_reason="download_http_404",
                ),
            ],
        }
        self.assertTrue(
            discord_media_reference_candidate_ledger_is_exact(
                record,
                source_url=source_url,
                failed_attempt_number=1,
            )
        )

        unseen = deepcopy(record)
        unseen_url = "https://media.discordapp.net/external/unseen"
        unseen["candidate_urls"].append(unseen_url)
        unseen["observations"].append(
            {
                "source": deepcopy(source),
                "url": unseen_url,
                "proxy_url": None,
                "metadata": {"url": unseen_url, "proxy_url": None},
            }
        )
        self.assertFalse(
            discord_media_reference_candidate_ledger_is_exact(
                unseen,
                source_url=source_url,
                failed_attempt_number=1,
            )
        )

        pending = deepcopy(record)
        pending["attempt_history"][-1].update(
            {"status": "in_progress", "terminal_reason": None}
        )
        self.assertFalse(
            discord_media_reference_candidate_ledger_is_exact(
                pending,
                source_url=source_url,
                failed_attempt_number=1,
            )
        )

    def test_historical_schema_candidate_defaults_and_identity_are_compatible(
        self,
    ) -> None:
        attempt = _binary_attempt(url=CANDIDATE_URL)
        current_missing = self._record([attempt])
        _bind_producer_metadata(
            current_missing,
            kind="attachment",
            field="attachment",
            metadata={
                "id": "item",
                "filename": "asset.bin",
                "size": 1,
                "content_type": "application/octet-stream",
            },
        )
        current_missing.pop("candidate_urls")
        validate_media_record_attempt_consistency(current_missing)

        historical_v2 = deepcopy(current_missing)
        historical_v2["schema_version"] = 2
        historical_v2["identity_metadata"] = {
            "id": "item",
            "filename": "asset.bin",
            "size": 1,
            "content_type": "application/octet-stream",
        }
        validate_media_record_attempt_consistency(historical_v2)

        forged_v2 = deepcopy(historical_v2)
        forged_v2["identity_metadata"]["filename"] = "forged.bin"
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(forged_v2)

    def test_identity_conflicts_are_rebuilt_and_complete_is_absorbing(self) -> None:
        complete = _binary_attempt(url=CANDIDATE_URL)
        conflict_record = self._record([complete])
        _bind_producer_metadata(
            conflict_record,
            kind="attachment",
            field="attachment",
            metadata={
                "id": "item",
                "size": 1,
                "content_type": "application/octet-stream",
            },
        )
        conflict_metadata = {
            "id": "item",
            "size": 999,
            "content_type": "application/octet-stream",
        }
        conflict_record["observations"].append(
            {
                "url": OTHER_URL,
                "proxy_url": None,
                "metadata": conflict_metadata,
            }
        )
        conflict_record["identity_conflicts"] = [
            {
                "observation_index": 1,
                "observed_identity": discord_media_identity_metadata(
                    "attachment",
                    conflict_metadata,
                ),
            }
        ]
        conflict_record.update(
            {
                "status": "failed",
                "terminal_reason": "logical_identity_conflict",
            }
        )
        validate_media_record_attempt_consistency(conflict_record)

        duplicated_observation = deepcopy(conflict_record)
        duplicated_observation["observations"].append(
            deepcopy(duplicated_observation["observations"][-1])
        )
        duplicated_observation["identity_conflicts"].append(
            {
                "observation_index": 2,
                "observed_identity": discord_media_identity_metadata(
                    "attachment",
                    conflict_metadata,
                ),
            }
        )
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(duplicated_observation)

        forged_complete = deepcopy(conflict_record)
        forged_complete.update(
            {
                "status": "complete",
                "terminal_reason": "downloaded",
            }
        )
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(forged_complete)

        forged_conflict = self._record([complete])
        forged_conflict.update(
            {
                "status": "failed",
                "terminal_reason": "logical_identity_conflict",
            }
        )
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(forged_conflict)

        later_failure = _attempt(
            url=OTHER_URL,
            terminal_reason="download_failed_transient",
        )
        complete_then_failure = self._record([complete, later_failure])
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(complete_then_failure)

    def test_hard_failure_attempts_are_absorbing_and_bound_to_current(self) -> None:
        size_limit = _attempt(
            url=CANDIDATE_URL,
            terminal_reason="size_limit_exceeded",
            actual_bytes=2,
        )
        size_limit["http_content_type"] = "application/octet-stream"
        size_limit_record = self._record([size_limit])
        validate_media_record_attempt_consistency(size_limit_record)

        later_complete = _binary_attempt(url=OTHER_URL)
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(
                self._record([size_limit, later_complete])
            )

        mismatched_current = deepcopy(size_limit_record)
        mismatched_current["terminal_reason"] = "download_failed_transient"
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(mismatched_current)

        conflict_metadata = {
            "id": "item",
            "size": 999,
            "content_type": None,
            "proxy_url": CANDIDATE_URL,
        }
        conflict_after_size_limit = deepcopy(size_limit_record)
        conflict_after_size_limit["observations"].append(
            {
                "url": OTHER_URL,
                "proxy_url": None,
                "metadata": conflict_metadata,
            }
        )
        conflict_after_size_limit["identity_conflicts"] = [
            {
                "observation_index": 1,
                "observed_identity": discord_media_identity_metadata(
                    "attachment",
                    conflict_metadata,
                ),
            }
        ]
        conflict_after_size_limit.update(
            {
                "status": "failed",
                "terminal_reason": "logical_identity_conflict",
            }
        )
        validate_media_record_attempt_consistency(conflict_after_size_limit)

    def test_warning_and_reference_cannot_hide_generic_pending_tail(self) -> None:
        warning = _binary_attempt(
            url=CANDIDATE_URL,
            status="captured_with_warning",
        )
        warning["terminal_reason"] = "declared_size_mismatch"
        warning_record = self._record([warning])
        _bind_producer_metadata(
            warning_record,
            kind="attachment",
            field="attachment",
            metadata={
                "id": "item",
                "size": 2,
                "content_type": "application/octet-stream",
            },
        )

        reference = _binary_attempt(
            url=CANDIDATE_URL,
            status="reference_only",
        )
        reference.update(
            {
                "terminal_reason": "media_reference_not_binary",
                "http_content_type": "text/html",
            }
        )
        reference_record = self._record([reference])
        _bind_producer_metadata(
            reference_record,
            kind="embed",
            field="video",
            metadata={"url": CANDIDATE_URL, "proxy_url": None},
        )

        for current_status, base_record in (
            ("captured_with_warning", warning_record),
            ("reference_only", reference_record),
        ):
            for pending_status, pending_reason in (
                ("in_progress", None),
                ("interrupted", "interrupted"),
            ):
                hidden_pending = deepcopy(base_record)
                hidden_pending["attempt_history"].append(
                    _attempt(
                        url=OTHER_URL,
                        status=pending_status,
                        terminal_reason=pending_reason,
                    )
                )
                with self.subTest(
                    current_status=current_status,
                    pending_status=pending_status,
                ), self.assertRaises(ValueError):
                    validate_media_record_attempt_consistency(hidden_pending)

    def test_reference_outcomes_preserve_binary_and_youtube_boundaries(self) -> None:
        media_reference = _binary_attempt(
            url=CANDIDATE_URL,
            status="reference_only",
        )
        media_reference.update(
            {
                "terminal_reason": "media_reference_not_binary",
                "http_content_type": "text/html",
            }
        )
        media_record = self._record([media_reference])
        _bind_producer_metadata(
            media_record,
            kind="embed",
            field="video",
            metadata={"url": CANDIDATE_URL, "proxy_url": None},
        )
        validate_media_record_attempt_consistency(media_record)

        zero_body_reference = deepcopy(media_record)
        zero_body_reference.update(
            {
                "actual_bytes": 0,
                "http_content_length": 0,
            }
        )
        zero_body_reference["attempt_history"][-1].update(
            {
                "actual_bytes": 0,
                "http_content_length": 0,
            }
        )
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(zero_body_reference)

        for label, field, value in (
            ("mismatched length", "http_content_length", 2),
            ("boolean length", "http_content_length", True),
            ("unnormalized MIME", "http_content_type", "Text/HTML; charset=utf-8"),
        ):
            tampered = deepcopy(media_record)
            tampered[field] = value
            tampered["attempt_history"][-1][field] = value
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(tampered)

        later_failure = _attempt(
            url=OTHER_URL,
            terminal_reason="download_http_404",
        )
        reference_after_failed_fallback = deepcopy(media_record)
        reference_after_failed_fallback["attempt_history"].append(later_failure)
        validate_media_record_attempt_consistency(reference_after_failed_fallback)

        later_complete = _binary_attempt(url=OTHER_URL)
        later_complete["http_content_type"] = "video/mp4"
        stale_reference = deepcopy(media_record)
        stale_reference["attempt_history"].append(later_complete)
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(stale_reference)

        youtube_url = "https://www.youtube.com/embed/SyntheticId"
        youtube_failure = _attempt(url=youtube_url)
        youtube_record = self._record([youtube_failure])
        youtube_record.update(
            {
                "url": youtube_url,
                "status": "reference_only",
                "terminal_reason": "youtube_embed_player_reference",
                "http_content_type": None,
                "http_content_length": None,
                "actual_bytes": 0,
                "sha256": None,
                "blob_path": None,
            }
        )
        _bind_producer_metadata(
            youtube_record,
            kind="embed",
            field="video",
            metadata={"url": youtube_url, "proxy_url": None},
        )
        validate_media_record_attempt_consistency(youtube_record)

    def test_current_unsafe_and_http_failures_must_mirror_global_tail(self) -> None:
        for terminal_reason in ("unsafe_media_url", "download_http_404"):
            attempt = _attempt(
                url=DIRECT_URL,
                terminal_reason=terminal_reason,
            )
            record = self._record([attempt])
            record["url"] = OTHER_URL
            with self.subTest(reason=terminal_reason), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(record)

            missing_tail = self._record([attempt])
            missing_tail["attempt_history"] = []
            with self.subTest(
                reason=terminal_reason,
                history="empty",
            ), self.assertRaises(ValueError):
                validate_media_record_attempt_consistency(missing_tail)

        unsafe = _attempt(url=DIRECT_URL, terminal_reason="unsafe_media_url")
        validate_media_record_attempt_consistency(self._record([unsafe]))

        partial_http = _attempt(
            url=DIRECT_URL,
            terminal_reason="download_http_404",
            actual_bytes=3,
        )
        partial_http.update(
            {
                "http_content_type": "image/png",
                "http_content_length": 10,
            }
        )
        validate_media_record_attempt_consistency(self._record([partial_http]))

    def test_legacy_marker_must_reference_the_immediately_previous_attempt(self) -> None:
        marker = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
            retry_trigger=LEGACY_RETRY_TRIGGER,
            retry_of_attempt_number=1,
        )
        record = self._record(
            [
                _attempt(),
                _attempt(url=OTHER_URL, terminal_reason="download_http_404"),
                marker,
            ]
        )

        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(record, context=self.context)
        self.assertIsNone(
            reusable_resolution_attempt_number(record, CANDIDATE_URL)
        )

        adjacent_marker = deepcopy(marker)
        adjacent = self._record([_attempt(), adjacent_marker])
        validate_resolution_attempt_history(adjacent, context=self.context)
        self.assertEqual(
            reusable_resolution_attempt_number(adjacent, CANDIDATE_URL),
            2,
        )

    def test_typed_sequence_one_after_latest_unsafe_requires_legacy_marker(
        self,
    ) -> None:
        def completed_record(
            attempts: list[dict[str, object]],
        ) -> dict[str, object]:
            record = self._record(attempts)
            _bind_producer_metadata(
                record,
                kind="attachment",
                field="attachment",
                metadata={
                    "id": "item",
                    "size": 1,
                    "content_type": "application/octet-stream",
                },
            )
            return record

        stripped = _binary_attempt(url=CANDIDATE_URL)
        stripped.update(
            {
                "policy_inputs_sha256": self.context.policy_inputs_sha256,
                "resolution_retry_sequence": 1,
            }
        )
        for label, unsafe in (
            ("legacy unsafe", _attempt()),
            (
                "fresh unsafe",
                {
                    **_attempt(),
                    "security_rejection": dict(
                        FRESH_SECURITY_REJECTION_PROVENANCE
                    ),
                },
            ),
        ):
            record = completed_record([unsafe, deepcopy(stripped)])
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_resolution_attempt_history(record, context=self.context)

        different_candidate = deepcopy(stripped)
        different_candidate["url"] = OTHER_URL
        validate_resolution_attempt_history(
            completed_record([_attempt(), different_candidate]),
            context=self.context,
        )

        old_http = _attempt(terminal_reason="download_http_404")
        validate_resolution_attempt_history(
            completed_record([_attempt(), old_http, deepcopy(stripped)]),
            context=self.context,
        )

        fresh_unsafe = {
            **_attempt(),
            "security_rejection": dict(FRESH_SECURITY_REJECTION_PROVENANCE),
        }
        washed_marker = completed_record(
            [
                fresh_unsafe,
                _attempt(terminal_reason="download_http_404"),
                deepcopy(stripped),
            ]
        )
        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                washed_marker,
                context=self.context,
            )

        other_candidate_success = _binary_attempt(url=OTHER_URL)
        validate_resolution_attempt_history(
            completed_record([fresh_unsafe, other_candidate_success]),
            context=self.context,
        )

    def test_typed_retry_selection_is_bound_to_current_global_tail(self) -> None:
        first_candidate = _typed_attempt(
            sequence=1,
            context=self.context,
            url=CANDIDATE_URL,
        )
        current_candidate = _typed_attempt(
            sequence=1,
            context=self.context,
            url=OTHER_URL,
        )
        record = self._record([first_candidate, current_candidate])

        self.assertEqual(
            next_resolution_retry_metadata(
                record,
                CANDIDATE_URL,
                context=self.context,
            ),
            {
                "retry_trigger": RESOLUTION_RETRY_TRIGGER,
                "retry_of_attempt_number": 1,
                "policy_inputs_sha256": self.context.policy_inputs_sha256,
                "resolution_retry_sequence": 2,
            },
        )
        self.assertEqual(
            next_resolution_retry_metadata(
                record,
                OTHER_URL,
                context=self.context,
            ),
            {
                "retry_trigger": RESOLUTION_RETRY_TRIGGER,
                "retry_of_attempt_number": 2,
                "policy_inputs_sha256": self.context.policy_inputs_sha256,
                "resolution_retry_sequence": 2,
            },
        )

        interposed_retry = _typed_attempt(
            sequence=2,
            context=self.context,
            url=CANDIDATE_URL,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
            retry_trigger=RESOLUTION_RETRY_TRIGGER,
            retry_of_attempt_number=1,
        )
        interposed = self._record(
            [first_candidate, current_candidate, interposed_retry]
        )
        validate_resolution_attempt_history(interposed, context=self.context)
        self.assertEqual(
            reusable_resolution_attempt_number(interposed, CANDIDATE_URL),
            3,
        )

    def test_sequence_gap_error_never_contains_signed_candidate_url(self) -> None:
        signed_url = (
            "https://media.discordapp.net/external/item"
            "?expiry_key=synthetic-expiry&signature_key=synthetic-signature"
        )
        attempts = [
            _typed_attempt(sequence=1, context=self.context, url=signed_url),
            _typed_attempt(
                sequence=3,
                context=self.context,
                url=signed_url,
                terminal_reason="media_resolution_retry_exhausted",
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
        ]

        with self.assertRaises(ValueError) as caught:
            validate_resolution_attempt_history(
                self._record(attempts),
                context=self.context,
            )

        message = str(caught.exception)
        for forbidden in (
            signed_url,
            "expiry_key",
            "synthetic-expiry",
            "signature_key",
            "synthetic-signature",
        ):
            self.assertNotIn(forbidden, message)

    def test_valid_legacy_sequence_one_and_transient_sequences_one_to_three(self) -> None:
        legacy = self._record(
            [
                _attempt(),
                _typed_attempt(
                    sequence=1,
                    context=self.context,
                    status="in_progress",
                    terminal_reason=None,
                    failure_detail=None,
                    retry_trigger=LEGACY_RETRY_TRIGGER,
                    retry_of_attempt_number=1,
                ),
            ]
        )
        validate_resolution_attempt_history(legacy, context=self.context)

        attempts = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=2,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
            _typed_attempt(
                sequence=3,
                context=self.context,
                terminal_reason="media_resolution_retry_exhausted",
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=2,
            ),
        ]
        validate_resolution_attempt_history(
            self._record(attempts),
            context=self.context,
        )

    def test_third_transient_sequence_is_exhausted_and_not_retried(self) -> None:
        attempts = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=2,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
            _typed_attempt(
                sequence=3,
                context=self.context,
                terminal_reason="media_resolution_retry_exhausted",
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=2,
            ),
        ]
        record = self._record(attempts)
        record["terminal_reason"] = "media_resolution_retry_exhausted"

        self.assertEqual(MAX_RESOLUTION_RETRY_SEQUENCES, 3)
        self.assertIsNone(
            next_resolution_retry_metadata(
                record,
                CANDIDATE_URL,
                context=self.context,
            )
        )

    def test_retryable_transport_failure_advances_typed_sequence(self) -> None:
        attempts = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=2,
                context=self.context,
                terminal_reason="download_failed_transient",
                failure_detail=None,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
        ]
        record = self._record(attempts)
        record["terminal_reason"] = "download_failed_transient"

        validate_resolution_attempt_history(record, context=self.context)
        self.assertEqual(
            next_resolution_retry_metadata(
                record,
                CANDIDATE_URL,
                context=self.context,
            ),
            {
                "retry_trigger": RESOLUTION_RETRY_TRIGGER,
                "retry_of_attempt_number": 2,
                "policy_inputs_sha256": self.context.policy_inputs_sha256,
                "resolution_retry_sequence": 3,
            },
        )

        pending = self._record(
            attempts
            + [
                _typed_attempt(
                    sequence=3,
                    context=self.context,
                    status="in_progress",
                    terminal_reason=None,
                    failure_detail=None,
                    retry_trigger=RESOLUTION_RETRY_TRIGGER,
                    retry_of_attempt_number=2,
                )
            ]
        )
        validate_resolution_attempt_history(pending, context=self.context)
        self.assertEqual(
            reusable_resolution_attempt_number(pending, CANDIDATE_URL),
            3,
        )

    def test_third_typed_content_length_failure_exhausts_budget(self) -> None:
        attempts = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=2,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
            _typed_attempt(
                sequence=3,
                context=self.context,
                terminal_reason="content_length_mismatch",
                failure_detail=None,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=2,
            ),
        ]
        record = self._record(attempts)
        record["terminal_reason"] = "content_length_mismatch"

        validate_resolution_attempt_history(record, context=self.context)
        self.assertIsNone(
            next_resolution_retry_metadata(
                record,
                CANDIDATE_URL,
                context=self.context,
            )
        )

    def test_typed_retry_rejects_an_intervening_generic_attempt(self) -> None:
        attempts = [
            _typed_attempt(sequence=1, context=self.context),
            _attempt(terminal_reason="download_failed_transient"),
            _typed_attempt(
                sequence=2,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
        ]

        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                self._record(attempts),
                context=self.context,
            )

    def test_reuses_committed_in_progress_and_interrupted_attempts(self) -> None:
        in_progress = self._record(
            [
                _typed_attempt(
                    sequence=1,
                    context=self.context,
                    status="in_progress",
                    terminal_reason=None,
                    failure_detail=None,
                )
            ]
        )
        self.assertEqual(
            reusable_resolution_attempt_number(in_progress, CANDIDATE_URL),
            1,
        )

        interrupted = self._record(
            [
                _attempt(url=OTHER_URL, terminal_reason="http_404"),
                _typed_attempt(
                    sequence=1,
                    context=self.context,
                    status="interrupted",
                    terminal_reason="interrupted",
                    failure_detail=None,
                ),
            ]
        )
        self.assertEqual(
            reusable_resolution_attempt_number(interrupted, CANDIDATE_URL),
            2,
        )
        self.assertIsNone(
            reusable_resolution_attempt_number(interrupted, CANDIDATE_URL + "2")
        )

    def test_retry_of_must_be_non_bool_backward_and_same_candidate(self) -> None:
        base = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=2,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
        ]
        for label, invalid_retry_of in (
            ("bool", True),
            ("zero", 0),
            ("forward", 2),
        ):
            attempts = deepcopy(base)
            attempts[1]["retry_of_attempt_number"] = invalid_retry_of
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_resolution_attempt_history(
                    self._record(attempts),
                    context=self.context,
                )

        attempts = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=1,
                context=self.context,
                url=OTHER_URL,
            ),
            _typed_attempt(
                sequence=2,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=2,
            ),
        ]
        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                self._record(attempts),
                context=self.context,
            )

    def test_legacy_retry_reference_requires_earlier_exact_zero_byte_unsafe(self) -> None:
        for label, mutate in (
            ("bool bytes", lambda attempt: attempt.update(actual_bytes=False)),
            ("wrong status", lambda attempt: attempt.update(status="complete")),
            (
                "wrong reason",
                lambda attempt: attempt.update(terminal_reason="http_404"),
            ),
            ("metadata", lambda attempt: attempt.update(http_content_type="text/html")),
        ):
            old = _attempt()
            mutate(old)
            marker = _typed_attempt(
                sequence=1,
                context=self.context,
                status="in_progress",
                terminal_reason=None,
                failure_detail=None,
                retry_trigger=LEGACY_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            )
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_resolution_attempt_history(
                    self._record([old, marker]),
                    context=self.context,
                )

    def test_committed_legacy_marker_revalidates_url_and_proxy_restrictions(self) -> None:
        evil_url = "https://evil.example/external/item?sig=synthetic"
        evil_record = self._record(
            [
                _attempt(url=evil_url),
                _typed_attempt(
                    sequence=1,
                    context=self.context,
                    url=evil_url,
                    status="in_progress",
                    terminal_reason=None,
                    failure_detail=None,
                    retry_trigger=LEGACY_RETRY_TRIGGER,
                    retry_of_attempt_number=1,
                ),
            ]
        )
        evil_record["declared_metadata"] = {"proxy_url": None}

        direct_record = self._record(
            [
                _attempt(url=DIRECT_URL),
                _typed_attempt(
                    sequence=1,
                    context=self.context,
                    url=DIRECT_URL,
                    status="in_progress",
                    terminal_reason=None,
                    failure_detail=None,
                    retry_trigger=LEGACY_RETRY_TRIGGER,
                    retry_of_attempt_number=1,
                ),
            ]
        )
        direct_record["declared_metadata"] = {"proxy_url": CANDIDATE_URL}

        for label, record in (
            ("unlisted host", evil_record),
            ("direct with external proxy", direct_record),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_resolution_attempt_history(
                    record,
                    context=self.context,
                )

    def test_legacy_marker_reference_must_be_latest_candidate_in_prefix(self) -> None:
        marker = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
            retry_trigger=LEGACY_RETRY_TRIGGER,
            retry_of_attempt_number=1,
        )
        record = self._record(
            [
                _attempt(),
                _attempt(terminal_reason="http_404"),
                marker,
            ]
        )

        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(record, context=self.context)

    def test_legacy_marker_prefix_must_not_contain_covered_binary(self) -> None:
        marker = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
            retry_trigger=LEGACY_RETRY_TRIGGER,
            retry_of_attempt_number=2,
        )
        record = self._record([_binary_attempt(), _attempt(), marker])

        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(record, context=self.context)

    def test_legacy_marker_prefix_rejects_covered_status_without_valid_binary(self) -> None:
        for covered_status in ("complete", "captured_with_warning"):
            covered = _attempt(
                url=OTHER_URL,
                status=covered_status,
                terminal_reason="downloaded",
            )
            marker = _typed_attempt(
                sequence=1,
                context=self.context,
                status="in_progress",
                terminal_reason=None,
                failure_detail=None,
                retry_trigger=LEGACY_RETRY_TRIGGER,
                retry_of_attempt_number=2,
            )
            record = self._record([covered, _attempt(), marker])

            with self.subTest(status=covered_status), self.assertRaises(ValueError):
                validate_resolution_attempt_history(
                    record,
                    context=self.context,
                )

    def test_binary_success_after_legacy_marker_remains_legal(self) -> None:
        completed_marker = _typed_attempt(
            sequence=1,
            context=self.context,
            status="complete",
            terminal_reason="downloaded",
            failure_detail=None,
            retry_trigger=LEGACY_RETRY_TRIGGER,
            retry_of_attempt_number=1,
        )
        completed_marker.update(_binary_attempt(url=CANDIDATE_URL))
        completed_marker.update(
            {
                "policy_inputs_sha256": self.context.policy_inputs_sha256,
                "resolution_retry_sequence": 1,
                "retry_trigger": LEGACY_RETRY_TRIGGER,
                "retry_of_attempt_number": 1,
            }
        )
        record = self._record([_attempt(), completed_marker])

        validate_resolution_attempt_history(record, context=self.context)

    def test_duplicate_legacy_marker_is_rejected(self) -> None:
        attempts = [
            _attempt(),
            _typed_attempt(
                sequence=1,
                context=self.context,
                status="interrupted",
                terminal_reason="interrupted",
                failure_detail=None,
                retry_trigger=LEGACY_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
            _attempt(),
            _typed_attempt(
                sequence=1,
                context=self.context,
                status="in_progress",
                terminal_reason=None,
                failure_detail=None,
                retry_trigger=LEGACY_RETRY_TRIGGER,
                retry_of_attempt_number=3,
            ),
        ]
        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                self._record(attempts),
                context=self.context,
            )

    def test_sequence_must_be_non_bool_unique_contiguous_and_at_most_three(self) -> None:
        duplicate = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(sequence=1, context=self.context),
        ]
        skipped = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=3,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
        ]
        over_budget = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=2,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
            _typed_attempt(
                sequence=3,
                context=self.context,
                terminal_reason="media_resolution_retry_exhausted",
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=2,
            ),
            _typed_attempt(
                sequence=4,
                context=self.context,
                terminal_reason="media_resolution_retry_exhausted",
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=3,
            ),
        ]
        bool_sequence = [_typed_attempt(sequence=True, context=self.context)]
        for label, attempts in (
            ("duplicate", duplicate),
            ("skipped", skipped),
            ("over budget", over_budget),
            ("bool", bool_sequence),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_resolution_attempt_history(
                    self._record(attempts),
                    context=self.context,
                )

    def test_policy_hash_must_match_opt_in_and_be_null_for_non_opt_in(self) -> None:
        opt_in_attempt = _typed_attempt(sequence=1, context=self.context)
        opt_in_attempt["policy_inputs_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                self._record([opt_in_attempt]),
                context=self.context,
            )

        non_opt_in = _context(opt_in=False)
        non_opt_in_attempt = _typed_attempt(sequence=1, context=non_opt_in)
        non_opt_in_attempt["policy_inputs_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                self._record([non_opt_in_attempt]),
                context=non_opt_in,
            )

    def test_directly_constructed_context_with_invalid_request_hash_fails_closed(self) -> None:
        invalid_context = replace(self.context, request_sha256=True)

        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                self._record([]),
                context=invalid_context,
            )

    def test_failure_details_and_terminal_reasons_have_exact_stable_pairs(self) -> None:
        illegal_pairs = (
            ("resolver_eai_again", "media_resolution_unresolved"),
            ("resolver_timeout", "media_resolution_invalid_answer"),
            ("resolver_name_not_found", "media_resolution_failed_transient"),
            ("resolver_no_data", "media_resolution_retry_exhausted"),
            ("resolver_empty_answer", "media_resolution_invalid_answer"),
            ("resolver_os_error_unclassified", "download_failed"),
            ("resolver_invalid_answer", "media_resolution_unresolved"),
            ("socket.gaierror: synthetic", "media_resolution_failed_transient"),
        )
        for detail, reason in illegal_pairs:
            attempt = _typed_attempt(
                sequence=1,
                context=self.context,
                failure_detail=detail,
                terminal_reason=reason,
            )
            with self.subTest(detail=detail, reason=reason), self.assertRaises(ValueError):
                validate_resolution_attempt_history(
                    self._record([attempt]),
                    context=self.context,
                )

    def test_typed_resolver_evidence_requires_zero_bytes_and_null_media_fields(self) -> None:
        tampered_fields = {
            "bool bytes": ("actual_bytes", False),
            "positive bytes": ("actual_bytes", 1),
            "content type": ("http_content_type", "text/html"),
            "content length": ("http_content_length", 0),
            "digest": ("sha256", "b" * 64),
            "blob": ("blob_path", "assets/blob.bin"),
        }
        for label, (field, value) in tampered_fields.items():
            attempt = _typed_attempt(sequence=1, context=self.context)
            attempt[field] = value
            record = self._record([attempt])
            record["terminal_reason"] = "media_resolution_failed_transient"
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    validate_resolution_attempt_history(
                        record,
                        context=self.context,
                    )
                with self.assertRaises(ValueError):
                    next_resolution_retry_metadata(
                        record,
                        CANDIDATE_URL,
                        context=self.context,
                    )

    def test_typed_unsafe_attempts_require_zero_bytes_and_null_media_fields(self) -> None:
        tampered_fields = {
            "positive bytes": ("actual_bytes", 1),
            "content type": ("http_content_type", "text/html"),
            "content length": ("http_content_length", 1),
            "digest": ("sha256", "b" * 64),
            "blob": ("blob_path", "assets/blob.bin"),
        }
        for retry_kind in ("legacy", "ordinary"):
            for label, (field, value) in tampered_fields.items():
                if retry_kind == "legacy":
                    unsafe = _typed_attempt(
                        sequence=1,
                        context=self.context,
                        status="failed",
                        terminal_reason="unsafe_media_url",
                        failure_detail=None,
                        retry_trigger=LEGACY_RETRY_TRIGGER,
                        retry_of_attempt_number=1,
                    )
                    attempts = [_attempt(), unsafe]
                else:
                    unsafe = _typed_attempt(
                        sequence=2,
                        context=self.context,
                        status="failed",
                        terminal_reason="unsafe_media_url",
                        failure_detail=None,
                        retry_trigger=RESOLUTION_RETRY_TRIGGER,
                        retry_of_attempt_number=1,
                    )
                    attempts = [
                        _typed_attempt(sequence=1, context=self.context),
                        unsafe,
                    ]
                unsafe[field] = value
                with self.subTest(retry_kind=retry_kind, field=label), self.assertRaises(ValueError):
                    validate_resolution_attempt_history(
                        self._record(attempts),
                        context=self.context,
                    )

    def test_pending_typed_states_have_exact_reason_detail_and_media_shape(self) -> None:
        valid_pending = (
            _typed_attempt(
                sequence=1,
                context=self.context,
                status="in_progress",
                terminal_reason=None,
                failure_detail=None,
            ),
            _typed_attempt(
                sequence=1,
                context=self.context,
                status="interrupted",
                terminal_reason="interrupted",
                failure_detail=None,
            ),
        )
        for attempt in valid_pending:
            validate_resolution_attempt_history(
                self._record([attempt]),
                context=self.context,
            )

        invalid_pending = {
            "in-progress terminal": _typed_attempt(
                sequence=1,
                context=self.context,
                status="in_progress",
                terminal_reason="downloaded",
                failure_detail=None,
            ),
            "interrupted terminal": _typed_attempt(
                sequence=1,
                context=self.context,
                status="interrupted",
                terminal_reason="downloaded",
                failure_detail=None,
            ),
            "in-progress detail": _typed_attempt(
                sequence=1,
                context=self.context,
                status="in_progress",
                terminal_reason=None,
                failure_detail="resolver_timeout",
            ),
            "failed without reason": _typed_attempt(
                sequence=1,
                context=self.context,
                status="failed",
                terminal_reason=None,
                failure_detail=None,
            ),
        }
        malformed_provenance = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
        )
        malformed_provenance.update(
            {
                "policy_inputs_sha256": False,
                "retry_trigger": "bogus",
                "retry_of_attempt_number": 999,
            }
        )
        invalid_pending["malformed provenance"] = malformed_provenance
        for label, attempt in invalid_pending.items():
            record = self._record([attempt])
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    validate_resolution_attempt_history(
                        record,
                        context=self.context,
                    )
                self.assertIsNone(
                    reusable_resolution_attempt_number(record, CANDIDATE_URL)
                )

    def test_reusable_retry_validates_its_referenced_history(self) -> None:
        valid_ordinary = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=2,
                context=self.context,
                status="in_progress",
                terminal_reason=None,
                failure_detail=None,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
        ]
        self.assertEqual(
            reusable_resolution_attempt_number(
                self._record(valid_ordinary),
                CANDIDATE_URL,
            ),
            2,
        )

        invalid_ordinary: dict[str, list[dict[str, object]]] = {
            "cross candidate": [
                _typed_attempt(
                    sequence=1,
                    context=self.context,
                    url=OTHER_URL,
                ),
                deepcopy(valid_ordinary[1]),
            ],
            "non transient": [
                _typed_attempt(
                    sequence=1,
                    context=self.context,
                    terminal_reason="media_resolution_unresolved",
                    failure_detail="resolver_name_not_found",
                ),
                deepcopy(valid_ordinary[1]),
            ],
            "wrong sequence": [
                _typed_attempt(sequence=2, context=self.context),
                deepcopy(valid_ordinary[1]),
            ],
            "bool reference": [
                deepcopy(valid_ordinary[0]),
                {
                    **deepcopy(valid_ordinary[1]),
                    "retry_of_attempt_number": True,
                },
            ],
            "forward reference": [
                deepcopy(valid_ordinary[0]),
                {
                    **deepcopy(valid_ordinary[1]),
                    "retry_of_attempt_number": 2,
                },
            ],
        }
        non_opt_in = _context(opt_in=False)
        missing_policy_reference = _typed_attempt(
            sequence=1,
            context=non_opt_in,
        )
        del missing_policy_reference["policy_inputs_sha256"]
        invalid_ordinary["missing referenced policy"] = [
            missing_policy_reference,
            _typed_attempt(
                sequence=2,
                context=non_opt_in,
                status="in_progress",
                terminal_reason=None,
                failure_detail=None,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
        ]
        for label, attempts in invalid_ordinary.items():
            with self.subTest(kind="ordinary", corruption=label):
                self.assertIsNone(
                    reusable_resolution_attempt_number(
                        self._record(attempts),
                        CANDIDATE_URL,
                    )
                )

        valid_legacy_marker = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail=None,
            retry_trigger=LEGACY_RETRY_TRIGGER,
            retry_of_attempt_number=1,
        )
        self.assertEqual(
            reusable_resolution_attempt_number(
                self._record([_attempt(), valid_legacy_marker]),
                CANDIDATE_URL,
            ),
            2,
        )

        legacy_after_newer_candidate = deepcopy(valid_legacy_marker)
        legacy_after_newer_candidate["retry_of_attempt_number"] = 1
        legacy_after_covered = deepcopy(valid_legacy_marker)
        legacy_after_covered["retry_of_attempt_number"] = 2
        invalid_legacy = {
            "unsafe not latest": [
                _attempt(),
                _attempt(terminal_reason="http_404"),
                legacy_after_newer_candidate,
            ],
            "covered prefix": [
                _attempt(
                    url=OTHER_URL,
                    status="complete",
                    terminal_reason="downloaded",
                ),
                _attempt(),
                legacy_after_covered,
            ],
        }
        for label, attempts in invalid_legacy.items():
            with self.subTest(kind="legacy", corruption=label):
                self.assertIsNone(
                    reusable_resolution_attempt_number(
                        self._record(attempts),
                        CANDIDATE_URL,
                    )
                )

    def test_transient_terminal_reason_is_bound_to_sequence_budget(self) -> None:
        early_exhausted = _typed_attempt(
            sequence=1,
            context=self.context,
            terminal_reason="media_resolution_retry_exhausted",
        )
        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                self._record([early_exhausted]),
                context=self.context,
            )

        attempts = [
            _typed_attempt(sequence=1, context=self.context),
            _typed_attempt(
                sequence=2,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=1,
            ),
            _typed_attempt(
                sequence=3,
                context=self.context,
                retry_trigger=RESOLUTION_RETRY_TRIGGER,
                retry_of_attempt_number=2,
            ),
        ]
        with self.assertRaises(ValueError):
            validate_resolution_attempt_history(
                self._record(attempts),
                context=self.context,
            )

    def test_resolution_terminal_outcome_requires_typed_sequence_and_detail(self) -> None:
        missing_sequence = _attempt(
            terminal_reason="media_resolution_unresolved"
        )
        missing_sequence["failure_detail"] = "resolver_name_not_found"
        missing_detail = _typed_attempt(
            sequence=1,
            context=self.context,
            terminal_reason="media_resolution_invalid_answer",
            failure_detail=None,
        )
        in_progress_detail = _typed_attempt(
            sequence=1,
            context=self.context,
            status="in_progress",
            terminal_reason=None,
            failure_detail="resolver_timeout",
        )
        for label, attempt in (
            ("missing sequence", missing_sequence),
            ("missing detail", missing_detail),
            ("in-progress detail", in_progress_detail),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_resolution_attempt_history(
                    self._record([attempt]),
                    context=self.context,
                )

    def test_schema_v3_history_without_new_typed_fields_remains_valid(self) -> None:
        record = self._record(
            [
                _attempt(),
                _attempt(
                    url=OTHER_URL,
                    status="failed",
                    terminal_reason="http_404",
                ),
            ]
        )

        validate_resolution_attempt_history(record, context=self.context)

    def test_next_retry_metadata_is_exact_for_legacy_and_transient_attempts(self) -> None:
        legacy = _legacy_record()
        self.assertEqual(
            next_resolution_retry_metadata(
                legacy,
                CANDIDATE_URL,
                context=self.context,
            ),
            {
                "retry_trigger": "legacy_resolver_security_conflation_v1",
                "retry_of_attempt_number": 1,
                "policy_inputs_sha256": self.context.policy_inputs_sha256,
                "resolution_retry_sequence": 1,
            },
        )

        transient = self._record(
            [
                _attempt(),
                _typed_attempt(
                    sequence=1,
                    context=self.context,
                    retry_trigger=LEGACY_RETRY_TRIGGER,
                    retry_of_attempt_number=1,
                ),
            ]
        )
        transient["terminal_reason"] = "media_resolution_failed_transient"
        self.assertEqual(
            next_resolution_retry_metadata(
                transient,
                CANDIDATE_URL,
                context=self.context,
            ),
            {
                "retry_trigger": "media_resolution_retry_v1",
                "retry_of_attempt_number": 2,
                "policy_inputs_sha256": self.context.policy_inputs_sha256,
                "resolution_retry_sequence": 2,
            },
        )

    def test_exact_candidate_url_including_query_is_the_retry_budget_key(self) -> None:
        attempts = [_typed_attempt(sequence=1, context=self.context)]
        record = self._record(attempts)
        different_query = CANDIDATE_URL.replace("synthetic", "different")

        self.assertIsNone(
            next_resolution_retry_metadata(
                record,
                different_query,
                context=self.context,
            )
        )

    def test_validation_and_selection_do_not_mutate_input_records(self) -> None:
        record = self._record(
            [_typed_attempt(sequence=1, context=self.context)]
        )
        before = deepcopy(record)

        validate_resolution_attempt_history(record, context=self.context)
        next_resolution_retry_metadata(
            record,
            CANDIDATE_URL,
            context=self.context,
        )
        reusable_resolution_attempt_number(record, CANDIDATE_URL)

        self.assertEqual(record, before)


class LegacyMediaCompatibilityMigrationTests(unittest.TestCase):
    @staticmethod
    def _canonical_sha256(value: object) -> str:
        content = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _migration() -> object:
        migration = getattr(
            media_recovery_module,
            "migrate_legacy_media_record",
            None,
        )
        if not callable(migration):
            raise AssertionError("legacy media migration helper is missing")
        return migration

    def _legacy_icon_record(
        self,
        *,
        field: str = "author_icon",
        current_url: str = CANDIDATE_URL,
    ) -> dict[str, object]:
        identity_key = "name" if field == "author_icon" else "text"
        identity_value = "analyst" if field == "author_icon" else "footer"
        metadata = {
            identity_key: identity_value,
            "icon_url": DIRECT_URL,
            "proxy_icon_url": CANDIDATE_URL,
        }
        source = {
            "message_id": "1",
            "channel_id": "2",
            "stream": "messages_2",
        }
        attempt = _binary_attempt(url=current_url)
        attempt["http_content_type"] = "image/png"
        record = {
            "schema_version": 3,
            "logical_key": f"1:embed:0:{field}",
            "kind": "embed",
            "field": field,
            "url": current_url,
            "candidate_urls": [DIRECT_URL, CANDIDATE_URL],
            "declared_metadata": deepcopy(metadata),
            "declared_content_type": None,
            "identity_metadata": deepcopy(metadata),
            "identity_conflicts": [],
            "sources": [deepcopy(source)],
            "observations": [
                {
                    "source": deepcopy(source),
                    "url": DIRECT_URL,
                    "proxy_url": None,
                    "metadata": deepcopy(metadata),
                }
            ],
            "observed_urls": [DIRECT_URL, CANDIDATE_URL],
            "attempt_history": [attempt],
            **{
                key: deepcopy(attempt[key])
                for key in (
                    "status",
                    "terminal_reason",
                    "http_content_type",
                    "http_content_length",
                    "actual_bytes",
                    "sha256",
                    "blob_path",
                )
            },
        }
        return record

    def _legacy_zero_complete(self) -> dict[str, object]:
        empty_sha = hashlib.sha256(b"").hexdigest()
        blob_path = f"assets/sha256/{empty_sha[:2]}/{empty_sha}.bin"
        source = {
            "message_id": "1",
            "channel_id": "2",
            "stream": "messages_2",
        }
        metadata = {
            "id": "item",
            "filename": "empty.png",
            "size": 0,
            "content_type": "image/png",
            "url": DIRECT_URL,
            "proxy_url": CANDIDATE_URL,
        }
        attempt = {
            "url": DIRECT_URL,
            "status": "complete",
            "terminal_reason": "downloaded",
            "http_content_type": "image/png",
            "http_content_length": 0,
            "actual_bytes": 0,
            "sha256": empty_sha,
            "blob_path": blob_path,
        }
        return {
            "schema_version": 3,
            "logical_key": "1:attachment:item",
            "kind": "attachment",
            "field": "attachment",
            "url": DIRECT_URL,
            "candidate_urls": [DIRECT_URL, CANDIDATE_URL],
            "declared_metadata": deepcopy(metadata),
            "declared_content_type": "image/png",
            "identity_metadata": {
                "id": "item",
                "size": 0,
                "content_type": "image/png",
            },
            "identity_conflicts": [],
            "sources": [deepcopy(source)],
            "observations": [
                {
                    "source": deepcopy(source),
                    "url": DIRECT_URL,
                    "proxy_url": CANDIDATE_URL,
                    "metadata": deepcopy(metadata),
                }
            ],
            "observed_urls": [DIRECT_URL, CANDIDATE_URL],
            "attempt_history": [attempt],
            **{
                key: deepcopy(attempt[key])
                for key in (
                    "status",
                    "terminal_reason",
                    "http_content_type",
                    "http_content_length",
                    "actual_bytes",
                    "sha256",
                    "blob_path",
                )
            },
        }

    def test_icon_v3_migration_binds_exact_icon_fields_without_changing_attempt(
        self,
    ) -> None:
        source = self._legacy_icon_record()
        attempt_before = json.dumps(
            source["attempt_history"][0],
            sort_keys=True,
            separators=(",", ":"),
        )

        migrated, changed = self._migration()(
            source,
            source_record_sha256=self._canonical_sha256(source),
            verified_empty_blob=False,
        )

        self.assertTrue(changed)
        self.assertEqual(migrated["schema_version"], 4)
        self.assertEqual(migrated["identity_metadata"], {"name": "analyst"})
        self.assertEqual(
            migrated["observations"][0]["proxy_url"],
            CANDIDATE_URL,
        )
        self.assertEqual(
            json.dumps(
                migrated["attempt_history"][0],
                sort_keys=True,
                separators=(",", ":"),
            ),
            attempt_before,
        )
        validate_media_record_attempt_consistency(migrated)

        second, changed_again = self._migration()(
            migrated,
            source_record_sha256=self._canonical_sha256(migrated),
            verified_empty_blob=False,
        )
        self.assertFalse(changed_again)
        self.assertEqual(second, migrated)

    def test_icon_v4_signature_rotation_is_authorized_but_name_change_conflicts(
        self,
    ) -> None:
        source = self._legacy_icon_record(current_url=DIRECT_URL)
        migrated, _ = self._migration()(
            source,
            source_record_sha256=self._canonical_sha256(source),
            verified_empty_blob=False,
        )
        rotated_direct = DIRECT_URL.replace("synthetic", "rotated")
        rotated_proxy = CANDIDATE_URL.replace("synthetic", "rotated")
        rotated = deepcopy(migrated)
        rotated_metadata = {
            "name": "analyst",
            "icon_url": rotated_direct,
            "proxy_icon_url": rotated_proxy,
        }
        rotated["observations"].append(
            {
                "source": deepcopy(rotated["sources"][0]),
                "url": rotated_direct,
                "proxy_url": rotated_proxy,
                "metadata": rotated_metadata,
            }
        )
        rotated["observed_urls"].extend([rotated_direct, rotated_proxy])
        validate_media_record_attempt_consistency(rotated)

        changed_name = deepcopy(rotated)
        changed_name["observations"][-1]["metadata"]["name"] = "other"
        with self.assertRaisesRegex(ValueError, "producer metadata"):
            validate_media_record_attempt_consistency(changed_name)

    def test_icon_v4_rejects_unobserved_arbitrary_url_key(self) -> None:
        source = self._legacy_icon_record(current_url=DIRECT_URL)
        migrated, _ = self._migration()(
            source,
            source_record_sha256=self._canonical_sha256(source),
            verified_empty_blob=False,
        )
        forged = deepcopy(migrated)
        forged_url = "https://attacker.invalid/not-observed"
        forged["url"] = forged_url
        forged["candidate_urls"] = [forged_url]
        forged["observed_urls"].append(forged_url)
        forged["declared_metadata"]["arbitrary_url"] = forged_url
        forged["observations"][0]["metadata"]["arbitrary_url"] = forged_url
        with self.assertRaisesRegex(ValueError, "producer metadata|migration"):
            validate_media_record_attempt_consistency(forged)

    def test_icon_migration_covers_single_direct_and_proxy_only_footer(self) -> None:
        direct = self._legacy_icon_record(current_url=DIRECT_URL)
        direct["candidate_urls"] = [DIRECT_URL]
        direct["declared_metadata"]["proxy_icon_url"] = DIRECT_URL
        direct["identity_metadata"]["proxy_icon_url"] = DIRECT_URL
        direct["observations"][0]["metadata"]["proxy_icon_url"] = DIRECT_URL
        migrated_direct, changed_direct = self._migration()(
            direct,
            source_record_sha256=self._canonical_sha256(direct),
            verified_empty_blob=False,
        )
        self.assertTrue(changed_direct)
        self.assertEqual(migrated_direct["candidate_urls"], [DIRECT_URL])
        self.assertEqual(migrated_direct["identity_metadata"], {"name": "analyst"})
        validate_media_record_attempt_consistency(migrated_direct)

        proxy_only = self._legacy_icon_record(
            field="footer_icon",
            current_url=CANDIDATE_URL,
        )
        proxy_only["candidate_urls"] = [CANDIDATE_URL]
        proxy_only["declared_metadata"].pop("icon_url")
        proxy_only["identity_metadata"].pop("icon_url")
        proxy_only["observations"][0]["metadata"].pop("icon_url")
        proxy_only["observations"][0]["url"] = CANDIDATE_URL
        migrated_proxy, changed_proxy = self._migration()(
            proxy_only,
            source_record_sha256=self._canonical_sha256(proxy_only),
            verified_empty_blob=False,
        )
        self.assertTrue(changed_proxy)
        self.assertEqual(migrated_proxy["candidate_urls"], [CANDIDATE_URL])
        self.assertEqual(migrated_proxy["identity_metadata"], {"text": "footer"})
        self.assertEqual(
            migrated_proxy["observations"][0]["proxy_url"],
            CANDIDATE_URL,
        )
        validate_media_record_attempt_consistency(migrated_proxy)

    def test_zero_byte_complete_migration_is_append_only_non_network_and_idempotent(
        self,
    ) -> None:
        source = self._legacy_zero_complete()
        source_attempt = deepcopy(source["attempt_history"][0])

        migrated, changed = self._migration()(
            source,
            source_record_sha256=self._canonical_sha256(source),
            verified_empty_blob=True,
        )

        self.assertTrue(changed)
        self.assertEqual(migrated["attempt_history"][0], source_attempt)
        self.assertEqual(len(migrated["attempt_history"]), 2)
        marker = migrated["attempt_history"][1]
        self.assertFalse(marker["evidence_reclassification"]["network_attempted"])
        self.assertEqual(marker["status"], "failed")
        self.assertEqual(marker["terminal_reason"], "download_failed_transient")
        self.assertEqual(migrated["status"], "failed")
        self.assertEqual(migrated["actual_bytes"], 0)
        self.assertIsNone(migrated["sha256"])
        self.assertIsNone(migrated["blob_path"])
        validate_media_record_attempt_consistency(migrated)

        second, changed_again = self._migration()(
            migrated,
            source_record_sha256=self._canonical_sha256(migrated),
            verified_empty_blob=False,
        )
        self.assertFalse(changed_again)
        self.assertEqual(second, migrated)

    def test_migration_markers_reject_forged_source_hashes_and_duplicates(self) -> None:
        icon_source = self._legacy_icon_record()
        icon, _ = self._migration()(
            icon_source,
            source_record_sha256=self._canonical_sha256(icon_source),
            verified_empty_blob=False,
        )
        forged_icon = deepcopy(icon)
        forged_icon["producer_migration"]["source_record_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "migration source"):
            validate_media_record_attempt_consistency(forged_icon)

        zero_source = self._legacy_zero_complete()
        zero, _ = self._migration()(
            zero_source,
            source_record_sha256=self._canonical_sha256(zero_source),
            verified_empty_blob=True,
        )
        forged_zero = deepcopy(zero)
        forged_zero["attempt_history"][1]["evidence_reclassification"][
            "source_record_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(ValueError, "reclassification source"):
            validate_media_record_attempt_consistency(forged_zero)

        duplicate = deepcopy(zero)
        duplicate["attempt_history"].append(
            deepcopy(duplicate["attempt_history"][1])
        )
        with self.assertRaises(ValueError):
            validate_media_record_attempt_consistency(duplicate)

    def test_icon_marker_source_binding_survives_later_observations(self) -> None:
        source = self._legacy_icon_record(current_url=DIRECT_URL)
        migrated, _ = self._migration()(
            source,
            source_record_sha256=self._canonical_sha256(source),
            verified_empty_blob=False,
        )
        rotated_direct = DIRECT_URL.replace("synthetic", "later")
        rotated_proxy = CANDIDATE_URL.replace("synthetic", "later")
        migrated["observations"].append(
            {
                "source": deepcopy(migrated["sources"][0]),
                "url": rotated_direct,
                "proxy_url": rotated_proxy,
                "metadata": {
                    "name": "analyst",
                    "icon_url": rotated_direct,
                    "proxy_icon_url": rotated_proxy,
                },
            }
        )
        migrated["observed_urls"].extend([rotated_direct, rotated_proxy])
        validate_media_record_attempt_consistency(migrated)

        forged = deepcopy(migrated)
        forged["producer_migration"]["source_record_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "migration source"):
            validate_media_record_attempt_consistency(forged)


if __name__ == "__main__":
    unittest.main()
