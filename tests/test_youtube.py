from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.connectors.youtube import extract_youtube_video_id, is_youtube_url


class YouTubeUrlTests(unittest.TestCase):
    def test_extracts_watch_url_video_id(self) -> None:
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_extracts_short_url_video_id(self) -> None:
        self.assertEqual(
            extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ?t=12"),
            "dQw4w9WgXcQ",
        )

    def test_extracts_shorts_video_id(self) -> None:
        self.assertEqual(
            extract_youtube_video_id("https://youtube.com/shorts/abc123"),
            "abc123",
        )

    def test_rejects_non_youtube_url(self) -> None:
        self.assertFalse(is_youtube_url("https://example.com/watch?v=dQw4w9WgXcQ"))


if __name__ == "__main__":
    unittest.main()
