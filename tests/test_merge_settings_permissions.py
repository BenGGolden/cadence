"""Tests for scripts/merge_settings_permissions.py — focus on the unmerge
(`--remove`) mode added for /cadence:uninstall.

The merge path is exercised indirectly via test_configure_linear.py; these
cover removal across the three known namespace shapes, foreign-entry
preservation, file deletion on reduce-to-empty, namespace-agnostic removal,
and the --dry-run no-write contract.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "merge_settings_permissions.py"


def _run(settings_path, *extra):
    args = [sys.executable, str(SCRIPT), "--settings-path", str(settings_path)]
    args += list(extra)
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8")


def _write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


class UnmergeTests(unittest.TestCase):
    def test_removes_across_all_namespace_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.local.json"
            _write_json(settings, {"permissions": {"allow": [
                "mcp__linear__list_issues",
                "mcp__linear-server__get_issue",
                "mcp__claude_ai_Linear__save_issue",
                "Bash(ls:*)",
            ]}})
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data["permissions"]["allow"], ["Bash(ls:*)"])

    def test_deletes_file_when_reduced_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.local.json"
            _write_json(settings, {"permissions": {"allow": [
                "mcp__claude_ai_Linear__list_issues",
                "mcp__claude_ai_Linear__save_comment",
            ]}})
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertFalse(settings.exists())

    def test_preserves_other_top_level_keys(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.local.json"
            _write_json(settings, {
                "permissions": {"allow": ["mcp__linear__get_issue"]},
                "model": "sonnet",
            })
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data["model"], "sonnet")
            # allow emptied -> allow and permissions pruned.
            self.assertNotIn("permissions", data)

    def test_works_without_namespace(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.local.json"
            _write_json(settings, {"permissions": {"allow": [
                "mcp__linear__list_issues", "Bash(git:*)",
            ]}})
            out = _run(settings, "--remove")  # no --namespace
            self.assertEqual(out.returncode, 0, msg=out.stderr)

    def test_missing_file_is_noop_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.local.json"
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertIn("nothing to remove", out.stdout)

    def test_unparseable_file_refused_exit_1_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.local.json"
            settings.write_text("not json", encoding="utf-8")
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 1)
            self.assertEqual(settings.read_text(encoding="utf-8"), "not json")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.local.json"
            _write_json(settings, {"permissions": {"allow": [
                "mcp__linear__list_issues",
            ]}})
            before = settings.read_text(encoding="utf-8")
            out = _run(settings, "--remove", "--dry-run")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertIn("[dry-run]", out.stdout)
            self.assertTrue(settings.exists())
            self.assertEqual(settings.read_text(encoding="utf-8"), before)

    def test_no_cadence_entries_reports_nothing_to_remove(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.local.json"
            _write_json(settings, {"permissions": {"allow": ["Bash(ls:*)"]}})
            before = settings.read_text(encoding="utf-8")
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertIn("nothing to remove", out.stdout)
            self.assertEqual(settings.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
