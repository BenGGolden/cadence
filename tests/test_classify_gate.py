"""Tests for templates/hooks/classify_gate.py.

The gate verdict decision is a pure function imported directly. Covers
waiting / approve / rework-under-cap / rework-at-cap (escalate) / both-labels-
present. The CLI path is smoke-tested via subprocess.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "templates" / "hooks"
SCRIPT = HOOKS / "classify_gate.py"
sys.path.insert(0, str(HOOKS))

import classify_gate  # noqa: E402

GATE = {"type": "gate", "on_approve": "implement", "on_rework": "plan",
        "linear_state": "Plan Review"}
GATE_CAPPED = dict(GATE, max_rework=2)


class ClassifyGateTests(unittest.TestCase):

    def test_waiting_neither_label(self):
        r = classify_gate.classify_gate(False, False, GATE, 0)
        self.assertEqual(r["verdict"], "waiting")
        self.assertIsNone(r["target_state"])
        self.assertEqual(r["remove_labels"], [])
        self.assertFalse(r["escalate"])

    def test_approve(self):
        r = classify_gate.classify_gate(True, False, GATE, 0)
        self.assertEqual(r["verdict"], "approve")
        self.assertEqual(r["target_state"], "implement")
        self.assertEqual(r["remove_labels"], ["cadence_approve"])
        self.assertFalse(r["escalate"])

    def test_rework_under_cap(self):
        r = classify_gate.classify_gate(False, True, GATE_CAPPED, 1)
        self.assertEqual(r["verdict"], "rework")
        self.assertEqual(r["target_state"], "plan")
        self.assertEqual(r["remove_labels"], ["cadence_rework"])
        self.assertFalse(r["escalate"])

    def test_rework_at_cap_escalates(self):
        r = classify_gate.classify_gate(False, True, GATE_CAPPED, 2)
        self.assertEqual(r["verdict"], "rework")
        self.assertTrue(r["escalate"])

    def test_rework_over_cap_escalates(self):
        r = classify_gate.classify_gate(False, True, GATE_CAPPED, 5)
        self.assertTrue(r["escalate"])

    def test_rework_no_max_rework_never_escalates(self):
        r = classify_gate.classify_gate(False, True, GATE, 99)
        self.assertEqual(r["verdict"], "rework")
        self.assertFalse(r["escalate"])

    def test_both_labels_treated_as_rework_removes_both(self):
        r = classify_gate.classify_gate(True, True, GATE, 0)
        self.assertEqual(r["verdict"], "rework")
        self.assertEqual(r["target_state"], "plan")
        self.assertEqual(set(r["remove_labels"]),
                         {"cadence_approve", "cadence_rework"})

    def test_both_labels_at_cap_escalates(self):
        r = classify_gate.classify_gate(True, True, GATE_CAPPED, 2)
        self.assertEqual(r["verdict"], "rework")
        self.assertTrue(r["escalate"])
        self.assertEqual(set(r["remove_labels"]),
                         {"cadence_approve", "cadence_rework"})

    # ---------- CLI ----------

    def test_cli_rework_escalates(self):
        with tempfile.TemporaryDirectory() as td:
            gc = Path(td) / "gate.json"
            gc.write_text(json.dumps(GATE_CAPPED), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--gate-config", str(gc),
                 "--rework-count", "2", "--rework"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertTrue(out["escalate"])


if __name__ == "__main__":
    unittest.main()
