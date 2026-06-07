"""Tests for scripts/merge_settings_hooks.py — merge and unmerge.

The merge path was previously untested (only exercised end-to-end via
/cadence:init); these cover the baseline merge plus the new `--remove`
(unmerge) mode added for /cadence:uninstall. Inputs are built in a
tempfile.TemporaryDirectory() and the script is invoked via subprocess so
the full argparse + I/O path runs.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "merge_settings_hooks.py"
TEMPLATE = REPO_ROOT / "templates" / "settings.json"

# A Cadence hook entry, shaped like what the template merges in.
_CADENCE_ENTRY = {
    "matcher": "Bash",
    "hooks": [{
        "type": "command",
        "command": 'python "$CLAUDE_PROJECT_DIR"/.claude/cadence/hooks/validate_tracking_json.py',
    }],
}
# A hand-added non-Cadence hook entry that must always survive.
_FOREIGN_ENTRY = {
    "matcher": "Edit",
    "hooks": [{"type": "command", "command": "/my/custom/lint.py"}],
}


def _run(settings_path, *extra, template=None):
    args = [sys.executable, str(SCRIPT), "--settings-path", str(settings_path)]
    if template is not None:
        args += ["--template-path", str(template)]
    args += list(extra)
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8")


def _write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


class MergeBaselineTests(unittest.TestCase):
    def test_merge_into_missing_file_creates_cadence_entries(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            out = _run(settings, template=TEMPLATE)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertIn("PreToolUse", data["hooks"])
            self.assertIn("UserPromptSubmit", data["hooks"])

    def test_merge_preserves_foreign_entry(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            _write_json(settings, {"hooks": {"PreToolUse": [_FOREIGN_ENTRY]}})
            out = _run(settings, template=TEMPLATE)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertIn(_FOREIGN_ENTRY, data["hooks"]["PreToolUse"])

    def test_merge_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            _run(settings, template=TEMPLATE)
            first = settings.read_text(encoding="utf-8")
            _run(settings, template=TEMPLATE)
            self.assertEqual(settings.read_text(encoding="utf-8"), first)

    def test_merge_missing_template_path_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            out = _run(settings)  # no --template-path, no --remove
            self.assertEqual(out.returncode, 1)
            self.assertIn("--template-path is required", out.stderr)


class UnmergeTests(unittest.TestCase):
    def test_removes_cadence_entries_preserves_foreign(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            _write_json(settings, {"hooks": {
                "PreToolUse": [_CADENCE_ENTRY, _FOREIGN_ENTRY],
                "UserPromptSubmit": [_CADENCE_ENTRY],
            }})
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data["hooks"]["PreToolUse"], [_FOREIGN_ENTRY])
            # UserPromptSubmit had only Cadence -> event dropped.
            self.assertNotIn("UserPromptSubmit", data["hooks"])

    def test_deletes_file_when_reduced_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            _write_json(settings, {"hooks": {"PreToolUse": [_CADENCE_ENTRY]}})
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertFalse(settings.exists())

    def test_keeps_file_with_non_cadence_content(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            _write_json(settings, {
                "hooks": {"PreToolUse": [_FOREIGN_ENTRY]},
                "model": "opus",
            })
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertTrue(settings.exists())
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data["model"], "opus")
            self.assertEqual(data["hooks"]["PreToolUse"], [_FOREIGN_ENTRY])

    def test_missing_file_is_noop_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertIn("nothing to remove", out.stdout)

    def test_unparseable_file_refused_exit_1_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            settings.write_text("{not json", encoding="utf-8")
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 1)
            self.assertEqual(settings.read_text(encoding="utf-8"), "{not json")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            _write_json(settings, {"hooks": {"PreToolUse": [_CADENCE_ENTRY]}})
            before = settings.read_text(encoding="utf-8")
            out = _run(settings, "--remove", "--dry-run")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertIn("[dry-run]", out.stdout)
            self.assertTrue(settings.exists())
            self.assertEqual(settings.read_text(encoding="utf-8"), before)

    def test_no_cadence_entries_reports_nothing_to_remove(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            _write_json(settings, {"hooks": {"PreToolUse": [_FOREIGN_ENTRY]}})
            before = settings.read_text(encoding="utf-8")
            out = _run(settings, "--remove")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertIn("nothing to remove", out.stdout)
            self.assertEqual(settings.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
