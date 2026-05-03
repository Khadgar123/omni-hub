from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.markdown import parse_markdown, split_frontmatter


MARKDOWN_FIXTURE = """---
title: "万象中枢设计"
reviewed: true
count: 3
---

# 万象中枢

连接 [[OpenAI]]、[[Obsidian|我的笔记]] 和 [GitHub](https://github.com)。

#ai #工作流
"""


class MarkdownTests(unittest.TestCase):
    def test_splits_frontmatter(self) -> None:
        metadata, body = split_frontmatter(MARKDOWN_FIXTURE)

        self.assertEqual(metadata["title"], "万象中枢设计")
        self.assertTrue(metadata["reviewed"])
        self.assertEqual(metadata["count"], 3)
        self.assertIn("# 万象中枢", body)

    def test_parse_markdown_extracts_links_and_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "note.md"
            path.write_text(MARKDOWN_FIXTURE, encoding="utf-8")

            document = parse_markdown(path, Path(tmpdir))

            self.assertEqual(document.title, "万象中枢")
            self.assertEqual(document.tags, ["ai", "工作流"])
            self.assertEqual(document.wiki_links, ["Obsidian", "OpenAI"])
            self.assertEqual(document.markdown_links[0]["url"], "https://github.com")


if __name__ == "__main__":
    unittest.main()
