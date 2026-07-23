"""W3C trace-context helper tests (refactor step 9)."""

from __future__ import annotations

import re
import unittest

from omni_hub.tracecontext import (
    make_traceparent,
    parse_traceparent,
    trace_headers,
)

_TP_RE = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-01$")
_UUID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"


class TraceContextTests(unittest.TestCase):
    def test_empty_or_none_yields_no_header(self) -> None:
        self.assertEqual(make_traceparent(None), "")
        self.assertEqual(make_traceparent(""), "")
        self.assertEqual(trace_headers(None), {})
        self.assertEqual(trace_headers(""), {})

    def test_valid_format_and_headers(self) -> None:
        tp = make_traceparent(_UUID)
        self.assertRegex(tp, _TP_RE)
        self.assertEqual(trace_headers(_UUID), {"traceparent": tp})

    def test_deterministic(self) -> None:
        # same trace_id -> identical header (retry-safe, no RNG)
        self.assertEqual(make_traceparent(_UUID), make_traceparent(_UUID))
        self.assertNotEqual(make_traceparent("a"), make_traceparent("b"))

    def test_round_trip_parse(self) -> None:
        tp = make_traceparent(_UUID)
        parsed = parse_traceparent(tp)
        assert parsed is not None
        self.assertEqual(parsed.version, "00")
        self.assertEqual(len(parsed.trace_id), 32)
        self.assertEqual(len(parsed.parent_id), 16)
        self.assertTrue(parsed.sampled)
        # uuid hex (dashes stripped) is reused as the trace-id
        self.assertEqual(parsed.trace_id, _UUID.replace("-", ""))

    def test_non_hex_trace_id_is_hashed_not_rejected(self) -> None:
        tp = make_traceparent("operation/xyz#123")
        self.assertRegex(tp, _TP_RE)

    def test_parse_rejects_malformed(self) -> None:
        self.assertIsNone(parse_traceparent(None))
        self.assertIsNone(parse_traceparent(""))
        self.assertIsNone(parse_traceparent("garbage"))
        self.assertIsNone(parse_traceparent("00-tooshort-x-01"))
        self.assertIsNone(parse_traceparent(f"00-{'0' * 32}-{'1' * 16}-01"))  # all-zero trace
        self.assertIsNone(parse_traceparent(f"00-{'a' * 32}-{'0' * 16}-01"))  # all-zero span


if __name__ == "__main__":
    unittest.main()
