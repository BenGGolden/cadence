"""Tests for templates/cadence/hooks/classify_drift.py.

The drift decision is a pure function imported directly. Covers the ordered
branch it applies, first match wins: null latest, Match,
forward-progression-via-`next`, and drift-otherwise. The CLI path is smoke-
tested via subprocess so the argparse + JSON-emit wiring stays exercised.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "templates" / "cadence" / "hooks"
SCRIPT = HOOKS / "classify_drift.py"
sys.path.insert(0, str(HOOKS))

import classify_drift  # noqa: E402

# Minimal states block: an agent (`plan`) with next, a gate (`plan_review`),
# and the agent it advances into (`implement`).
STATES = {
    "plan": {"type": "agent", "next": "plan_review", "linear_state": "Planning"},
    "plan_review": {"type": "gate", "on_approve": "implement",
                    "on_rework": "plan", "linear_state": "Plan Review"},
    "implement": {"type": "agent", "next": "agent_review",
                  "linear_state": "Implementing"},
}


class ClassifyDriftTests(unittest.TestCase):

    def test_null_latest_no_drift(self):
        r = classify_drift.classify_drift(None, "plan", "Planning", STATES)
        self.assertFalse(r["drift"])
        self.assertIsNone(r["reconcile_args"])

    def test_match_no_drift(self):
        r = classify_drift.classify_drift("implement", "implement",
                                          "Implementing", STATES)
        self.assertFalse(r["drift"])
        self.assertIsNone(r["reconcile_args"])

    def test_forward_progression_no_drift(self):
        # latest was `plan`; matched is plan's `next` (plan_review).
        r = classify_drift.classify_drift("plan", "plan_review",
                                          "Plan Review", STATES)
        self.assertFalse(r["drift"])
        self.assertIsNone(r["reconcile_args"])

    def test_drift_otherwise(self):
        # latest was `plan`; matched is `implement` (NOT plan's next).
        r = classify_drift.classify_drift("plan", "implement",
                                          "Implementing", STATES)
        self.assertTrue(r["drift"])
        self.assertEqual(r["reconcile_args"], {
            "observed_linear_state": "Implementing",
            "expected_state": "plan",
            "reason": "human reassigned",
        })

    def test_forward_progression_only_via_next_not_gate_targets(self):
        # latest was a gate (`plan_review`); a gate has no `next`, so the
        # forward-progression rule does not fire and this is drift.
        r = classify_drift.classify_drift("plan_review", "implement",
                                          "Implementing", STATES)
        self.assertTrue(r["drift"])

    def test_unknown_latest_state_is_drift(self):
        # latest names a state not in config → no `next` to match → drift.
        r = classify_drift.classify_drift("ghost", "implement",
                                          "Implementing", STATES)
        self.assertTrue(r["drift"])
        self.assertEqual(r["reconcile_args"]["expected_state"], "ghost")

    # ---------- CLI ----------

    def test_cli_emits_json(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "cfg.json"
            cfg.write_text(json.dumps({"states": STATES}), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-config", str(cfg),
                 "--matched-state", "implement",
                 "--current-column", "Implementing",
                 "--latest-state", "plan"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertTrue(out["drift"])

    def test_cli_omitted_latest_is_null_no_drift(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "cfg.json"
            cfg.write_text(json.dumps({"states": STATES}), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-config", str(cfg),
                 "--matched-state", "plan",
                 "--current-column", "Planning"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertFalse(out["drift"])


if __name__ == "__main__":
    unittest.main()
