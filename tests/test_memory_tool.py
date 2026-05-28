"""Tests for v0.17-J Memory Tool surface (Anthropic memory_20250818)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.memory_tool import (
    MemoryTool,
    MemoryToolError,
    TOOL_VERSION,
    dispatch,
)


class PathPolicyTests(unittest.TestCase):
    def test_paths_must_start_with_memories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            with self.assertRaises(MemoryToolError):
                tool.create("/elsewhere/foo.md", "x")

    def test_traversal_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            with self.assertRaises(MemoryToolError):
                tool.create("/memories/../escape.md", "x")
            with self.assertRaises(MemoryToolError):
                tool.create("/memories/./escape.md", "x")

    def test_view_unknown_path_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            with self.assertRaises(MemoryToolError):
                tool.view("/memories/does-not-exist.md")


class CommandsTests(unittest.TestCase):
    def test_create_then_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            tool.create("/memories/notes/hello.md", "line1\nline2\nline3\n")
            response = tool.view("/memories/notes/hello.md")
            self.assertTrue(response.ok)
            self.assertEqual(response.detail["type"], "file")
            # splitlines() reports 3 (trailing '\n' is the line terminator,
            # not a 4th line) — matches Anthropic's text-editor convention.
            self.assertEqual(response.detail["total_lines"], 3)
            self.assertIn("line2", response.detail["annotated"])

    def test_view_directory_lists_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            tool.create("/memories/a.md", "a")
            tool.create("/memories/b.md", "b")
            response = tool.view("/memories")
            self.assertEqual(response.detail["type"], "directory")
            self.assertEqual(sorted(response.detail["entries"]), ["a.md", "b.md"])

    def test_view_range_slices_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            tool.create("/memories/log.md", "one\ntwo\nthree\nfour\nfive\n")
            response = tool.view("/memories/log.md", view_range=(2, 4))
            self.assertEqual(response.detail["view_range"], [2, 4])
            self.assertIn("two", response.detail["annotated"])
            self.assertIn("three", response.detail["annotated"])
            self.assertIn("four", response.detail["annotated"])
            self.assertNotIn("five", response.detail["annotated"])

    def test_view_range_neg_one_means_eof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            tool.create("/memories/log.md", "a\nb\nc\nd\n")
            response = tool.view("/memories/log.md", view_range=(2, -1))
            self.assertIn("b", response.detail["annotated"])
            self.assertIn("d", response.detail["annotated"])

    def test_str_replace_unique_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            tool.create("/memories/notes.md", "foo bar foo")
            with self.assertRaises(MemoryToolError):
                tool.str_replace("/memories/notes.md", "foo", "baz")

    def test_str_replace_unique_match_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            tool.create("/memories/notes.md", "alpha beta gamma")
            tool.str_replace("/memories/notes.md", "beta", "BETA")
            body = (Path(tmp) / "vault" / "memory" / "notes.md").read_text(encoding="utf-8")
            self.assertEqual(body, "alpha BETA gamma")

    def test_insert_inserts_at_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            tool.create("/memories/log.md", "one\ntwo\nthree")
            tool.insert("/memories/log.md", 1, "ONE-AND-A-HALF")
            body = (Path(tmp) / "vault" / "memory" / "log.md").read_text(encoding="utf-8")
            self.assertIn("one\nONE-AND-A-HALF\ntwo", body)

    def test_delete_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            tool.create("/memories/temp.md", "x")
            tool.delete("/memories/temp.md")
            self.assertFalse((Path(tmp) / "vault" / "memory" / "temp.md").exists())

    def test_rename_moves_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = MemoryTool(tmp)
            tool.create("/memories/a.md", "x")
            tool.rename("/memories/a.md", "/memories/b.md")
            self.assertFalse((Path(tmp) / "vault" / "memory" / "a.md").exists())
            self.assertTrue((Path(tmp) / "vault" / "memory" / "b.md").exists())


class DispatcherTests(unittest.TestCase):
    def test_dispatch_routes_to_correct_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            response = dispatch(tmp, "create", arguments={
                "path": "/memories/foo.md", "file_text": "hello",
            })
            self.assertTrue(response.ok)
            payload = response.to_dict()
            self.assertEqual(payload["tool"], TOOL_VERSION)
            self.assertEqual(payload["command"], "create")

    def test_dispatch_rejects_unknown_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MemoryToolError):
                dispatch(tmp, "nope", arguments={"path": "/memories/x"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
