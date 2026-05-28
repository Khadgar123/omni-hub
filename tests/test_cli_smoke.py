"""CLI smoke tests (v0.40) — catch argparse format-string / registration
regressions before they hit ``omni-hub --help``.

Added in response to a 2026-05-28 review that found a stray ``%`` in
``cli/finance.py`` help text broke parser construction (argparse treats
``%`` as a format spec).  The class of bug is recurring and easy to hit
when adding new CLI subcommands; a single test calling ``build_parser()``
catches it at test time, not at user-invocation time.
"""

from __future__ import annotations

import argparse
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.cli import build_parser


class BuildParserSmokeTests(unittest.TestCase):
    def test_build_parser_runs_without_error(self) -> None:
        """``build_parser()`` must construct the full subparser tree.

        Any malformed ``help=`` / ``description=`` (e.g. unescaped ``%``,
        unclosed ``%(prog)s`` references) raises during ``add_argument``
        before tests can run anything else.
        """

        parser = build_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)

    def test_subcommands_each_print_help_without_format_errors(self) -> None:
        """For every registered subcommand, exercise ``--help`` so any
        format-string in its help/description fires now, not at user
        invocation time."""

        parser = build_parser()
        # argparse subparsers live in a single _SubParsersAction.
        sub_action = next(
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        for name, subparser in sorted(sub_action.choices.items()):
            with self.subTest(subcommand=name):
                buf = io.StringIO()
                try:
                    with redirect_stdout(buf), redirect_stderr(buf):
                        try:
                            subparser.parse_args(["--help"])
                        except SystemExit:
                            # argparse exits with 0 after printing --help;
                            # any other exit code is a real error.
                            pass
                except (TypeError, ValueError) as exc:
                    self.fail(
                        f"subcommand {name!r} --help raised "
                        f"{type(exc).__name__}: {exc}.  Likely a stray "
                        f"`%` in help= text (use `25 pct` or `%%`)."
                    )

    def test_known_v039_subcommands_are_registered(self) -> None:
        """The 22 v0.39 subcommands from users/scheduling/inbox/projects/
        pptx/finance modules must all be discoverable."""

        parser = build_parser()
        sub_action = next(
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        choices = set(sub_action.choices.keys())
        for required in (
            # users
            "user-list", "user-enroll", "user-approve",
            "user-set-persona", "user-memory-recall", "user-memory-archival",
            # scheduling
            "cal-add", "cal-list",
            "personal-task-add", "personal-task-list", "personal-task-done",
            "schedule-plan",
            # inbox
            "inbox-classify",
            # projects
            "project-create", "project-list", "project-show",
            # pptx
            "pptx-build",
            # finance
            "finance-screen", "finance-watch-create",
            "finance-watch-list", "finance-portfolio-stats", "order-propose",
        ):
            self.assertIn(required, choices,
                           f"v0.40 smoke: missing subcommand {required!r}")


class HelpStringHygieneTests(unittest.TestCase):
    """Static scan of all help= strings for the ``%`` foot-gun pattern."""

    def test_no_unescaped_percent_in_cli_help(self) -> None:
        """Any literal ``%`` in a help string (other than ``%%`` or
        ``%(prog)s`` etc.) is a latent argparse failure."""

        import re
        cli_dir = Path(__file__).resolve().parents[1] / "src" / "omni_hub" / "cli"
        offenders: list[tuple[str, int, str]] = []
        # Match raw `%` not followed by a paren / % / s / d (the legal
        # format specs argparse + python-string-percent accept).
        bad_pct = re.compile(r"%(?![%(sd])")
        # Find help=" ... " or help='...' literals.
        help_literal = re.compile(
            r"""help\s*=\s*(["'])((?:\\.|(?!\1).)*?)\1""",
        )
        for path in cli_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                for m in help_literal.finditer(line):
                    body = m.group(2)
                    if bad_pct.search(body):
                        offenders.append((str(path.name), lineno, body[:80]))
        self.assertEqual(
            offenders, [],
            "stray %% in CLI help — argparse will treat as format spec.  "
            "Use literal text like '25 pct' or escape as '%%'.  Offenders: "
            + repr(offenders),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
