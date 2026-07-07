"""Tests for templates/cadence/hooks/classify_merge.py.

The merge-on-approve outcome decision is two pure functions imported directly:
classify_after_read (no_pr / already_merged / attempt_merge / escalate) and
classify_after_merge (merged / failed). The tests pin both the `decision` and
the exact side-effect combination each returns — advancing decisions move to
the terminal and never escalate; halting decisions escalate and never move;
every terminal decision releases the lock. The CLI path is smoke-tested via
subprocess through the argparse + config-load + I/O route.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "templates" / "cadence" / "hooks"
SCRIPT = HOOKS / "classify_merge.py"
sys.path.insert(0, str(HOOKS))

import classify_merge  # noqa: E402

LABELS = {
    "cadence_active": "cadence:active",
    "cadence_needs_human": "cadence:needs-human",
    "cadence_approve": "cadence:approve",
    "cadence_rework": "cadence:rework",
}
GATE_STATE = "review"
MERGE_TARGET = "Done"
PR_URL = "https://github.com/o/r/pull/7"


def _types(actions):
    return [a["type"] for a in actions]


def _labels_removed(actions):
    return [a["label"] for a in actions if a["type"] == "remove_label"]


def _labels_added(actions):
    return [a["label"] for a in actions if a["type"] == "add_label"]


def _moves(actions):
    return [a["linear_state"] for a in actions if a["type"] == "move_state"]


def _bodies(actions):
    return [a["body"] for a in actions if a["type"] == "post_comment"]


def _read(pr_url=PR_URL, pr_state=None, read_error=None):
    return classify_merge.classify_after_read(
        pr_url, pr_state, read_error, LABELS, GATE_STATE, MERGE_TARGET)


def _merge(merge_result=None, merge_error=None):
    return classify_merge.classify_after_merge(
        PR_URL, merge_result, merge_error, LABELS, GATE_STATE, MERGE_TARGET)


class ClassifyAfterReadTests(unittest.TestCase):

    def test_read_no_pr(self):
        r = _read(pr_url="")
        self.assertEqual(r["decision"], "no_pr")
        self.assertIn(LABELS["cadence_needs_human"], _labels_added(r["actions"]))
        self.assertIn(LABELS["cadence_active"], _labels_removed(r["actions"]))
        self.assertEqual(_moves(r["actions"]), [])
        self.assertEqual(len(_bodies(r["actions"])), 1)

    def test_read_error_escalates(self):
        r = _read(read_error="boom")
        self.assertEqual(r["decision"], "escalate")
        self.assertIn(LABELS["cadence_needs_human"], _labels_added(r["actions"]))
        self.assertIn(LABELS["cadence_active"], _labels_removed(r["actions"]))
        self.assertEqual(_moves(r["actions"]), [])

    def test_read_pr_state_not_dict_escalates(self):
        r = _read(pr_state=None)
        self.assertEqual(r["decision"], "escalate")
        self.assertEqual(_moves(r["actions"]), [])

    def test_read_already_merged(self):
        r = _read(pr_state={"state": "closed", "merged": True})
        self.assertEqual(r["decision"], "already_merged")
        self.assertEqual(_moves(r["actions"]), [MERGE_TARGET])
        self.assertIn(LABELS["cadence_active"], _labels_removed(r["actions"]))
        self.assertEqual(_labels_added(r["actions"]), [])

    def test_read_open_attempts_merge(self):
        r = _read(pr_state={"state": "open", "merged": False})
        self.assertEqual(r["decision"], "attempt_merge")
        self.assertEqual(r["actions"], [])

    def test_read_closed_not_merged_escalates(self):
        r = _read(pr_state={"state": "closed", "merged": False})
        self.assertEqual(r["decision"], "escalate")
        self.assertIn(LABELS["cadence_needs_human"], _labels_added(r["actions"]))
        self.assertEqual(_moves(r["actions"]), [])
        # The failure body names the observed state.
        self.assertIn("closed", _bodies(r["actions"])[0])

    def test_read_error_wins_over_pr_state(self):
        # Both supplied → prefer the error arg (branch order guarantees it).
        r = _read(pr_state={"state": "open", "merged": False},
                  read_error="transient")
        self.assertEqual(r["decision"], "escalate")


class ClassifyAfterMergeTests(unittest.TestCase):

    def test_merge_success(self):
        r = _merge(merge_result={"merged": True})
        self.assertEqual(r["decision"], "merged")
        self.assertEqual(_moves(r["actions"]), [MERGE_TARGET])
        self.assertIn(LABELS["cadence_active"], _labels_removed(r["actions"]))
        self.assertEqual(_labels_added(r["actions"]), [])

    def test_merge_error_fails(self):
        r = _merge(merge_error="conflict")
        self.assertEqual(r["decision"], "failed")
        self.assertIn(LABELS["cadence_needs_human"], _labels_added(r["actions"]))
        self.assertIn(LABELS["cadence_active"], _labels_removed(r["actions"]))
        self.assertEqual(_moves(r["actions"]), [])

    def test_merge_non_merged_result_fails(self):
        r = _merge(merge_result={"merged": False, "message": "blocked"})
        self.assertEqual(r["decision"], "failed")
        self.assertEqual(_moves(r["actions"]), [])
        self.assertIn("blocked", _bodies(r["actions"])[0])

    def test_merge_error_wins_over_result(self):
        r = _merge(merge_result={"merged": True}, merge_error="race")
        self.assertEqual(r["decision"], "failed")


class InvariantTests(unittest.TestCase):
    """Cross-decision invariants — the side-effect combination is the point."""

    # (decision, result dict) for every terminal decision.
    _TERMINAL_READS = [
        ("no_pr", _read(pr_url="")),
        ("escalate", _read(read_error="x")),
        ("already_merged", _read(pr_state={"state": "closed", "merged": True})),
    ]
    _TERMINAL_MERGES = [
        ("merged", _merge(merge_result={"merged": True})),
        ("failed", _merge(merge_error="x")),
    ]

    def test_every_terminal_decision_removes_active(self):
        for name, r in self._TERMINAL_READS + self._TERMINAL_MERGES:
            with self.subTest(decision=name):
                self.assertIn(LABELS["cadence_active"],
                              _labels_removed(r["actions"]),
                              f"{name} must release the lock")

    def test_attempt_merge_is_the_only_no_action_decision(self):
        r = _read(pr_state={"state": "open", "merged": False})
        self.assertEqual(r["actions"], [])
        self.assertEqual(r["decision"], "attempt_merge")

    def test_advancing_decisions_move_and_never_escalate(self):
        for name, r in (("already_merged",
                         _read(pr_state={"state": "closed", "merged": True})),
                        ("merged", _merge(merge_result={"merged": True}))):
            with self.subTest(decision=name):
                self.assertEqual(_moves(r["actions"]), [MERGE_TARGET])
                self.assertEqual(_labels_added(r["actions"]), [])

    def test_halting_decisions_escalate_and_never_move(self):
        halting = [
            _read(pr_url=""),                                    # no_pr
            _read(read_error="x"),                               # escalate
            _read(pr_state={"state": "closed", "merged": False}),  # escalate
            _merge(merge_error="x"),                             # failed
            _merge(merge_result={"merged": False}),             # failed
        ]
        for r in halting:
            with self.subTest(decision=r["decision"]):
                self.assertIn(LABELS["cadence_needs_human"],
                              _labels_added(r["actions"]))
                self.assertEqual(_moves(r["actions"]), [])

    def test_bodies_are_canonical_tracking_comments(self):
        # Each posted body is produced by emit_tracking_comment.build_merge:
        # it starts with the merge marker and its embedded JSON `status` is the
        # expected merge status for that decision. status == decision for
        # no_pr / already_merged / merged / failed; escalate maps to a `failed`
        # body (there is no `escalate` status); attempt_merge posts no body.
        expected_status = {
            "no_pr": "no_pr",
            "already_merged": "already_merged",
            "merged": "merged",
            "failed": "failed",
            "escalate": "failed",
        }
        cases = [
            _read(pr_url=""),
            _read(read_error="x"),
            _read(pr_state={"state": "closed", "merged": True}),
            _read(pr_state={"state": "closed", "merged": False}),
            _merge(merge_result={"merged": True}),
            _merge(merge_error="x"),
            _merge(merge_result={"merged": False}),
        ]
        for r in cases:
            with self.subTest(decision=r["decision"]):
                bodies = _bodies(r["actions"])
                self.assertEqual(len(bodies), 1)
                body = bodies[0]
                self.assertTrue(body.startswith("<!-- cadence:merge "),
                                f"body not a merge tracking comment: {body!r}")
                payload = json.loads(body.split(" -->", 1)[0]
                                     .removeprefix("<!-- cadence:merge "))
                self.assertEqual(payload["status"],
                                 expected_status[r["decision"]])


class CliTests(unittest.TestCase):

    def _config(self, td):
        cfg = Path(td) / "cfg.json"
        cfg.write_text(json.dumps({"label": LABELS}), encoding="utf-8")
        return cfg

    def test_cli_read_already_merged(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._config(td)
            pr = Path(td) / "pr-read.json"
            pr.write_text(json.dumps({"state": "closed", "merged": True}),
                          encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--phase", "read",
                 "--pr-url", PR_URL, "--pr-state-json", str(pr),
                 "--state", GATE_STATE, "--merge-target", MERGE_TARGET,
                 "--workflow-config", str(cfg)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["decision"], "already_merged")
            self.assertEqual(_moves(out["actions"]), [MERGE_TARGET])

    def test_cli_read_no_pr_needs_neither_state_arg(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._config(td)
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--phase", "read",
                 "--pr-url", "",
                 "--state", GATE_STATE, "--merge-target", MERGE_TARGET,
                 "--workflow-config", str(cfg)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(json.loads(r.stdout)["decision"], "no_pr")

    def test_cli_merge_success(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = self._config(td)
            mr = Path(td) / "merge-result.json"
            mr.write_text(json.dumps({"merged": True}), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--phase", "merge",
                 "--pr-url", PR_URL, "--merge-result-json", str(mr),
                 "--state", GATE_STATE, "--merge-target", MERGE_TARGET,
                 "--workflow-config", str(cfg)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(json.loads(r.stdout)["decision"], "merged")


if __name__ == "__main__":
    unittest.main()
