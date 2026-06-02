"""Tests for scripts/render_uninstall_steps.py.

A byte-identical fixture comparison guards the operator-facing Linear-cleanup
checklist against accidental edits (mirrors test_render_next_steps.py), plus a
check that all four cadence-* labels are named.
"""
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "render_uninstall_steps.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "uninstall"


def _run():
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, encoding="utf-8",
    )


class FixtureByteIdentityTests(unittest.TestCase):
    def test_matches_fixture(self):
        out = _run()
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        expected = (FIXTURES / "linear_cleanup.txt").read_text(encoding="utf-8")
        self.assertEqual(out.stdout, expected)


class ContentTests(unittest.TestCase):
    def test_names_all_four_cadence_labels(self):
        out = _run()
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        for label in ("cadence-active", "cadence-needs-human",
                      "cadence-approve", "cadence-rework"):
            self.assertIn(label, out.stdout)

    def test_mentions_workflow_columns_and_no_linear_calls(self):
        out = _run()
        self.assertIn("workflow.yaml", out.stdout)
        self.assertIn("never touches your Linear", out.stdout)


if __name__ == "__main__":
    unittest.main()
