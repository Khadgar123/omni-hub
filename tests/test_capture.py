from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.connectors.web import extract_html_metadata, html_to_text
from omni_hub.models import OperationSpec, OperationStatus, RiskLevel
from omni_hub.runner import OperationRunner


HTML_FIXTURE = """
<!doctype html>
<html>
  <head>
    <title>Fallback Title</title>
    <meta property="og:title" content="Captured Page">
    <meta name="description" content="A useful test page.">
    <link rel="canonical" href="https://example.com/canonical">
  </head>
  <body>
    <h1>Hello</h1>
    <script>ignoreMe()</script>
    <p>World</p>
  </body>
</html>
"""


class CaptureTests(unittest.TestCase):
    def test_extracts_html_metadata_and_text(self) -> None:
        metadata = extract_html_metadata(HTML_FIXTURE)

        self.assertEqual(metadata.title, "Captured Page")
        self.assertEqual(metadata.description, "A useful test page.")
        self.assertEqual(metadata.canonical_url, "https://example.com/canonical")
        self.assertIn("Hello", html_to_text(HTML_FIXTURE))
        self.assertNotIn("ignoreMe", html_to_text(HTML_FIXTURE))

    def test_capture_url_operation_writes_raw_and_inbox_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )

            result = runner.run(
                OperationSpec(
                    name="capture_url",
                    connector="web",
                    action="capture_url",
                    payload={
                        "url": "https://example.com/article",
                        "html": HTML_FIXTURE,
                        "note": "manual test note",
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            self.assertEqual(result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(result.output["source_kind"], "webpage")
            self.assertEqual(result.output["title"], "Captured Page")
            self.assertTrue((Path(tmpdir) / result.output["raw_path"]).exists())
            markdown_path = Path(tmpdir) / result.output["markdown_path"]
            self.assertTrue(markdown_path.exists())
            self.assertIn("manual test note", markdown_path.read_text(encoding="utf-8"))

    def test_capture_youtube_url_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )

            result = runner.run(
                OperationSpec(
                    name="capture_url",
                    connector="web",
                    action="capture_url",
                    payload={
                        "url": "https://youtu.be/dQw4w9WgXcQ",
                        "fetch": False,
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            self.assertEqual(result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(result.output["source_kind"], "youtube_video")
            markdown = (Path(tmpdir) / result.output["markdown_path"]).read_text(
                encoding="utf-8"
            )
            self.assertIn("YouTube Video ID: dQw4w9WgXcQ", markdown)

    def test_capture_rejects_non_http_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )

            result = runner.run(
                OperationSpec(
                    name="capture_url",
                    connector="web",
                    action="capture_url",
                    payload={
                        "url": "file:///etc/passwd",
                        "fetch": False,
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )

            self.assertEqual(result.status, OperationStatus.FAILED)
            self.assertIn("only http and https", result.error)


if __name__ == "__main__":
    unittest.main()
