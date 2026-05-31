"""Tests for templates/hooks/classify_merge.py.

The merge-on-approve PR-state decision is a pure function imported directly.
Covers OPEN -> merge, MERGED -> advance, CLOSED -> escalate, and the
missing/garbage/unreadable-input escalation paths. The CLI path is smoke-tested
via subprocess.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "templates" / "hooks"
SCRIPT = HOOKS / "classify_merge.py"
sys.path.insert(0, str(HOOKS))

import classify_merge  # noqa: E402


class ClassifyMergeTests(unittest.TestCase):

    def test_open_merges(self):
        r = classify_merge.classify_merge({"state": "OPEN", "url": "u"})
        self.assertEqual(r["action"], "merge")
        self.assertIsInstance(r["reason"], str)

    def test_merged_advances(self):
        r = classify_merge.classify_merge({"state": "MERGED", "url": "u"})
        self.assertEqual(r["action"], "advance")
        self.assertIn("already merged", r["reason"])

    def test_closed_escalates(self):
        r = classify_merge.classify_merge({"state": "CLOSED", "url": "u"})
        self.assertEqual(r["action"], "escalate")
        self.assertIn("closed", r["reason"])

    def test_missing_state_escalates(self):
        r = classify_merge.classify_merge({"url": "u"})
        self.assertEqual(r["action"], "escalate")

    def test_unrecognized_state_escalates(self):
        r = classify_merge.classify_merge({"state": "DRAFT"})
        self.assertEqual(r["action"], "escalate")

    def test_non_dict_escalates(self):
        self.assertEqual(classify_merge.classify_merge(None)["action"],
                         "escalate")
        self.assertEqual(classify_merge.classify_merge("OPEN")["action"],
                         "escalate")
        self.assertEqual(classify_merge.classify_merge(["OPEN"])["action"],
                         "escalate")

    # ---------- CLI ----------

    def test_cli_open(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "pr.json"
            f.write_text(json.dumps({"state": "OPEN", "url": "u"}),
                         encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(f)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(json.loads(r.stdout)["action"], "merge")

    def test_cli_merged(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "pr.json"
            f.write_text(json.dumps({"state": "MERGED"}), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(f)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(json.loads(r.stdout)["action"], "advance")

    def test_cli_unreadable_input_escalates_exit_0(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does-not-exist.json"
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(missing)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(json.loads(r.stdout)["action"], "escalate")

    def test_cli_garbage_input_escalates_exit_0(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "pr.json"
            f.write_text("not json {{{", encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(f)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(json.loads(r.stdout)["action"], "escalate")


if __name__ == "__main__":
    unittest.main()
