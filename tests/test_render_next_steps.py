"""Tests for scripts/render_next_steps.py.

Two byte-identical fixture comparisons cover the broad-stroke acceptance
criteria (success path, detection-failure path) — these guard
against any future edit to the renderer that changes the visible
operator handoff. Surrounding tests exercise the bool-flag parsing, the
indentation of multi-line permissions blocks, and the omit-vs-include
behaviour of the settings.local.json line.
"""
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "render_next_steps.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "init"

# The canonical Cadence verb allowlist as emitted by
# merge_settings_permissions.py --print-only. Keep this in sync with
# CADENCE_VERBS in that script — the two fixtures embed a fully expanded
# version under different namespaces.
_CADENCE_VERBS = (
    "list_issues",
    "get_issue",
    "list_comments",
    "create_comment",
    "save_comment",
    "update_issue",
    "save_issue",
    "add_label",
    "remove_label",
)


def _permissions_block(namespace):
    return "\n".join(f"mcp__{namespace}__{v}" for v in _CADENCE_VERBS)


def _run(*, settings_local_written, detection_note, permissions_block):
    return subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--settings-local-written", settings_local_written,
            "--permissions-detection-note", detection_note,
            "--permissions-block", permissions_block,
        ],
        capture_output=True, text=True, encoding="utf-8",
    )


class FixtureByteIdentityTests(unittest.TestCase):
    """Byte-identical comparison against the stored golden Markdown."""

    def test_success_path_matches_fixture(self):
        out = _run(
            settings_local_written="true",
            detection_note="Detected Linear MCP namespace: linear",
            permissions_block=_permissions_block("linear"),
        )
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        expected = (FIXTURES / "next_steps_success.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(out.stdout, expected)

    def test_failure_path_matches_fixture(self):
        detection_note = (
            "No Linear MCP server detected. Substitute "
            "<REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE> below with your "
            "actual namespace (see README \"Linear MCP tools\" for the "
            "three variants in the wild), then add each line to your "
            ".claude/settings.local.json permissions.allow array."
        )
        out = _run(
            settings_local_written="false",
            detection_note=detection_note,
            permissions_block=_permissions_block(
                "REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE"
            ),
        )
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        expected = (FIXTURES / "next_steps_failure.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(out.stdout, expected)


class SettingsLocalLineTests(unittest.TestCase):
    """The settings.local.json line is conditional on the bool flag."""

    def test_true_includes_settings_local_line(self):
        out = _run(
            settings_local_written="true",
            detection_note="Detected Linear MCP namespace: linear",
            permissions_block="mcp__linear__list_issues",
        )
        self.assertEqual(out.returncode, 0)
        self.assertIn(
            "  .claude/settings.local.json (Linear MCP allowlist merged in)",
            out.stdout,
        )

    def test_false_omits_settings_local_line(self):
        out = _run(
            settings_local_written="false",
            detection_note="Whatever",
            permissions_block="mcp__linear__list_issues",
        )
        self.assertEqual(out.returncode, 0)
        # The "Files written" entry must not appear. The unrelated
        # "Cloud /schedule routines do NOT read .claude/settings.local.json"
        # line still references the path and stays put.
        self.assertNotIn(
            "  .claude/settings.local.json (Linear MCP allowlist merged in)",
            out.stdout,
        )

    def test_case_insensitive_bool_parse(self):
        out = _run(
            settings_local_written="TRUE",
            detection_note="Note",
            permissions_block="mcp__linear__list_issues",
        )
        self.assertEqual(out.returncode, 0)
        self.assertIn("settings.local.json", out.stdout)

    def test_bad_bool_exits_1(self):
        out = _run(
            settings_local_written="yes",
            detection_note="Note",
            permissions_block="mcp__linear__list_issues",
        )
        self.assertEqual(out.returncode, 1)
        self.assertIn("--settings-local-written", out.stderr)


class PermissionsBlockIndentationTests(unittest.TestCase):
    def test_multi_line_block_each_line_indented(self):
        block = "mcp__linear__list_issues\nmcp__linear__get_issue"
        out = _run(
            settings_local_written="true",
            detection_note="Detected Linear MCP namespace: linear",
            permissions_block=block,
        )
        self.assertEqual(out.returncode, 0)
        # Both lines must appear indented by two spaces under the section
        # header (the indent makes the block read as a code-style listing
        # in the operator's terminal).
        self.assertIn("  mcp__linear__list_issues", out.stdout)
        self.assertIn("  mcp__linear__get_issue", out.stdout)

    def test_single_line_block_indented(self):
        out = _run(
            settings_local_written="true",
            detection_note="Detected Linear MCP namespace: linear",
            permissions_block="mcp__linear__list_issues",
        )
        self.assertEqual(out.returncode, 0)
        self.assertIn("  mcp__linear__list_issues", out.stdout)


class DetectionNoteTests(unittest.TestCase):
    def test_note_appears_verbatim_under_section_header(self):
        out = _run(
            settings_local_written="true",
            detection_note="Custom detection note here",
            permissions_block="mcp__linear__list_issues",
        )
        self.assertEqual(out.returncode, 0)
        # Indented two spaces under the section header.
        self.assertIn("  Custom detection note here", out.stdout)

    def test_section_header_always_present(self):
        out = _run(
            settings_local_written="false",
            detection_note="Whatever",
            permissions_block="mcp__linear__list_issues",
        )
        self.assertEqual(out.returncode, 0)
        self.assertIn(
            "Permissions for /schedule routines (paste into the routine's "
            "permissions panel):",
            out.stdout,
        )


class CliArgValidationTests(unittest.TestCase):
    def test_missing_required_arg_exits_2(self):
        # argparse exits 2 on missing required args.
        out = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
