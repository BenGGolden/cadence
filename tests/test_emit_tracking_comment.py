"""Tests for templates/cadence/hooks/emit_tracking_comment.py.

Covers every documented --kind x --status combination, the missing-arg
exit-1 paths, error-string truncation/newline collapsing, and that the
emitted JSON parses cleanly.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "cadence" / "hooks" / "emit_tracking_comment.py"


# Body shape: "<!-- cadence:<prefix> {json} -->\n<visible markdown>"
BODY_RE = re.compile(
    r"^<!--\s+cadence:(?P<prefix>state|gate|reconcile|sweep|merge|warning)\s+"
    r"(?P<json>\{.*?\})\s+-->\n(?P<visible>.*)$",
    re.DOTALL,
)


def run_emit(*args):
    # Decode as UTF-8: the warning kind's visible line carries a ⚠️, and the
    # script forces UTF-8 stdout regardless of the platform locale.
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8")


def parse_body(stdout):
    m = BODY_RE.match(stdout.rstrip("\n"))
    assert m, f"unexpected body shape: {stdout!r}"
    return m.group("prefix"), json.loads(m.group("json")), m.group("visible")


class EmitTrackingCommentTests(unittest.TestCase):

    # ---------- state ----------

    def test_state_attempt_marker(self):
        r = run_emit("--kind", "state", "--state", "implement",
                     "--attempt", "2",
                     "--started-at", "2026-05-26T12:00:00Z")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "state")
        self.assertEqual(payload, {
            "state": "implement", "attempt": 2,
            "started_at": "2026-05-26T12:00:00Z",
        })
        self.assertIn("implement", visible)
        self.assertIn("attempt 2", visible)

    def test_state_failure_record(self):
        r = run_emit("--kind", "state", "--state", "implement",
                     "--attempt", "2", "--status", "failed",
                     "--error", "boom",
                     "--subagent", "implementer")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "state")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "boom")
        self.assertEqual(payload["attempt"], 2)
        self.assertIn("implementer", visible)

    def test_state_failure_truncates_error_at_400_chars(self):
        long_err = "x" * 500
        r = run_emit("--kind", "state", "--state", "implement",
                     "--attempt", "1", "--status", "failed",
                     "--error", long_err)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        _, payload, _ = parse_body(r.stdout)
        self.assertEqual(len(payload["error"]), 400)

    def test_state_failure_collapses_newlines(self):
        r = run_emit("--kind", "state", "--state", "implement",
                     "--attempt", "1", "--status", "failed",
                     "--error", "line1\nline2\r\nline3")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        _, payload, _ = parse_body(r.stdout)
        self.assertNotIn("\n", payload["error"])
        self.assertNotIn("\r", payload["error"])
        self.assertEqual(payload["error"], "line1 line2 line3")

    def test_state_failure_blank_error_normalises_to_empty_string(self):
        r = run_emit("--kind", "state", "--state", "implement",
                     "--attempt", "1", "--status", "failed")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        _, payload, _ = parse_body(r.stdout)
        self.assertEqual(payload["error"], "")

    # ---------- gate ----------

    def test_gate_waiting(self):
        r = run_emit("--kind", "gate", "--state", "plan_review",
                     "--status", "waiting")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "gate")
        self.assertEqual(payload, {"state": "plan_review", "status": "waiting"})
        self.assertIn("plan_review", visible)

    def test_gate_rework(self):
        r = run_emit("--kind", "gate", "--state", "plan_review",
                     "--status", "rework", "--rework-to", "plan")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "gate")
        self.assertEqual(payload, {
            "state": "plan_review", "status": "rework", "rework_to": "plan",
        })
        self.assertIn("plan", visible)

    def test_gate_escalated(self):
        r = run_emit("--kind", "gate", "--state", "human_review",
                     "--status", "escalated")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "gate")
        self.assertEqual(payload, {
            "state": "human_review", "status": "escalated",
        })
        self.assertIn("human_review", visible)

    # ---------- merge ----------

    def test_merge_merged(self):
        r = run_emit("--kind", "merge", "--state", "human_review",
                     "--status", "merged",
                     "--pr-url", "https://github.com/o/r/pull/7")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "merge")
        self.assertEqual(payload, {
            "state": "human_review", "status": "merged",
            "pr_url": "https://github.com/o/r/pull/7",
        })
        self.assertIn("Merged PR", visible)
        self.assertIn("https://github.com/o/r/pull/7", visible)

    def test_merge_already_merged(self):
        r = run_emit("--kind", "merge", "--state", "human_review",
                     "--status", "already_merged",
                     "--pr-url", "https://github.com/o/r/pull/7")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "merge")
        self.assertEqual(payload["status"], "already_merged")
        self.assertEqual(payload["pr_url"], "https://github.com/o/r/pull/7")
        self.assertIn("already merged", visible)

    def test_merge_failed_cleans_error(self):
        r = run_emit("--kind", "merge", "--state", "human_review",
                     "--status", "failed",
                     "--error", "required check\nstill\r\nrunning")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "merge")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "required check still running")
        self.assertIn("Needs human intervention", visible)

    def test_merge_failed_truncates_error_at_400_chars(self):
        r = run_emit("--kind", "merge", "--state", "human_review",
                     "--status", "failed", "--error", "x" * 500)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        _, payload, _ = parse_body(r.stdout)
        self.assertEqual(len(payload["error"]), 400)

    def test_merge_no_pr(self):
        r = run_emit("--kind", "merge", "--state", "human_review",
                     "--status", "no_pr")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "merge")
        self.assertEqual(payload, {"state": "human_review", "status": "no_pr"})
        self.assertIn("no PR", visible)
        self.assertIn("human_review", visible)

    def test_merge_missing_state_exits_1(self):
        r = run_emit("--kind", "merge", "--status", "no_pr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--state", r.stderr)

    def test_merge_missing_status_exits_1(self):
        r = run_emit("--kind", "merge", "--state", "human_review")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--status", r.stderr)

    def test_merge_merged_missing_pr_url_exits_1(self):
        r = run_emit("--kind", "merge", "--state", "human_review",
                     "--status", "merged")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--pr-url", r.stderr)

    def test_merge_rejects_gate_status(self):
        # `waiting` is a valid --status choice but illegal for --kind merge.
        r = run_emit("--kind", "merge", "--state", "human_review",
                     "--status", "waiting")
        self.assertEqual(r.returncode, 1)
        self.assertIn("merge", r.stderr)

    # ---------- reconcile ----------

    def test_reconcile_record(self):
        r = run_emit("--kind", "reconcile",
                     "--observed-linear-state", "Done",
                     "--expected-state", "implement",
                     "--reason", "human moved column")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "reconcile")
        self.assertEqual(payload, {
            "observed_linear_state": "Done",
            "expected_state": "implement",
            "reason": "human moved column",
        })
        self.assertIn("Cadence", visible)

    # ---------- sweep ----------

    def test_sweep_record(self):
        r = run_emit("--kind", "sweep",
                     "--cleared-at", "2026-05-26T12:00:00Z",
                     "--last-activity", "2026-05-26T11:00:00Z",
                     "--stale-minutes", "60",
                     "--threshold-minutes", "30")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        prefix, payload, visible = parse_body(r.stdout)
        self.assertEqual(prefix, "sweep")
        self.assertEqual(payload, {
            "cleared_at": "2026-05-26T12:00:00Z",
            "last_activity": "2026-05-26T11:00:00Z",
            "stale_minutes": 60,
        })
        self.assertIn("Stale lock cleared", visible)
        self.assertIn("2026-05-26T11:00:00Z", visible)
        self.assertIn("60 minutes ago", visible)
        self.assertIn("threshold 30 minutes", visible)

    def test_sweep_rejects_non_integer_stale_minutes(self):
        r = run_emit("--kind", "sweep",
                     "--cleared-at", "2026-05-26T12:00:00Z",
                     "--last-activity", "2026-05-26T11:00:00Z",
                     "--stale-minutes", "sixty",
                     "--threshold-minutes", "30")
        # argparse type=int rejects non-integer values with exit 2.
        self.assertNotEqual(r.returncode, 0)

    def test_sweep_rejects_non_integer_threshold_minutes(self):
        r = run_emit("--kind", "sweep",
                     "--cleared-at", "2026-05-26T12:00:00Z",
                     "--last-activity", "2026-05-26T11:00:00Z",
                     "--stale-minutes", "60",
                     "--threshold-minutes", "thirty")
        self.assertNotEqual(r.returncode, 0)

    def test_sweep_missing_cleared_at_exits_1(self):
        r = run_emit("--kind", "sweep",
                     "--last-activity", "2026-05-26T11:00:00Z",
                     "--stale-minutes", "60",
                     "--threshold-minutes", "30")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--cleared-at", r.stderr)

    def test_sweep_missing_last_activity_exits_1(self):
        r = run_emit("--kind", "sweep",
                     "--cleared-at", "2026-05-26T12:00:00Z",
                     "--stale-minutes", "60",
                     "--threshold-minutes", "30")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--last-activity", r.stderr)

    def test_sweep_missing_stale_minutes_exits_1(self):
        r = run_emit("--kind", "sweep",
                     "--cleared-at", "2026-05-26T12:00:00Z",
                     "--last-activity", "2026-05-26T11:00:00Z",
                     "--threshold-minutes", "30")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--stale-minutes", r.stderr)

    def test_sweep_missing_threshold_minutes_exits_1(self):
        r = run_emit("--kind", "sweep",
                     "--cleared-at", "2026-05-26T12:00:00Z",
                     "--last-activity", "2026-05-26T11:00:00Z",
                     "--stale-minutes", "60")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--threshold-minutes", r.stderr)

    def test_sweep_zero_stale_minutes_renders_cleanly(self):
        r = run_emit("--kind", "sweep",
                     "--cleared-at", "2026-05-26T12:00:00Z",
                     "--last-activity", "2026-05-26T12:00:00Z",
                     "--stale-minutes", "0",
                     "--threshold-minutes", "30")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        _, payload, visible = parse_body(r.stdout)
        self.assertEqual(payload["stale_minutes"], 0)
        self.assertIn("0 minutes ago", visible)

    # ---------- error paths (exit 1 via die()) ----------

    def test_kind_state_missing_state_exits_1(self):
        r = run_emit("--kind", "state",
                     "--attempt", "1",
                     "--started-at", "2026-05-26T12:00:00Z")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--state", r.stderr)

    def test_kind_state_attempt_marker_missing_attempt_exits_1(self):
        r = run_emit("--kind", "state", "--state", "implement",
                     "--started-at", "2026-05-26T12:00:00Z")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--attempt", r.stderr)

    def test_kind_state_attempt_marker_missing_started_at_exits_1(self):
        r = run_emit("--kind", "state", "--state", "implement",
                     "--attempt", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--started-at", r.stderr)

    def test_kind_state_failure_missing_attempt_exits_1(self):
        r = run_emit("--kind", "state", "--state", "implement",
                     "--status", "failed", "--error", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--attempt", r.stderr)

    def test_kind_state_with_gate_status_exits_1(self):
        # --status waiting is valid argparse-wise but illegal for kind=state.
        r = run_emit("--kind", "state", "--state", "implement",
                     "--status", "waiting")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--kind state only supports", r.stderr)

    def test_kind_gate_missing_status_exits_1(self):
        r = run_emit("--kind", "gate", "--state", "plan_review")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--status", r.stderr)

    def test_kind_gate_rework_missing_rework_to_exits_1(self):
        r = run_emit("--kind", "gate", "--state", "plan_review",
                     "--status", "rework")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--rework-to", r.stderr)

    def test_kind_gate_with_state_failed_exits_1(self):
        # `failed` is a valid choice for --status but not for --kind gate.
        r = run_emit("--kind", "gate", "--state", "plan_review",
                     "--status", "failed")
        self.assertEqual(r.returncode, 1)
        self.assertIn("gate", r.stderr)

    def test_kind_reconcile_missing_args_exits_1(self):
        r = run_emit("--kind", "reconcile",
                     "--observed-linear-state", "Done")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--expected-state", r.stderr)
        self.assertIn("--reason", r.stderr)

    # ---------- warning ----------

    def _write_warning_file(self, td, data):
        wf = Path(td) / "context-warning.json"
        wf.write_text(json.dumps(data), encoding="utf-8")
        return wf

    def test_warning_from_file(self):
        message = ("Parent context for ENG-10 is 9481 chars, over the "
                   "4000-char soft budget. Move project-wide rules to "
                   ".claude/prompts/global.md.")
        with tempfile.TemporaryDirectory() as td:
            wf = self._write_warning_file(
                td, {"parent": "ENG-10", "chars": 9481, "message": message})
            r = run_emit("--kind", "warning", "--warning-file", str(wf))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            prefix, payload, visible = parse_body(r.stdout)
            self.assertEqual(prefix, "warning")
            self.assertEqual(payload, {"parent": "ENG-10", "chars": 9481})
            self.assertIn("9481 chars", visible)
            self.assertIn("⚠", visible)
            self.assertIn("[Cadence]", visible)

    def test_warning_requires_warning_file(self):
        r = run_emit("--kind", "warning")
        self.assertEqual(r.returncode, 1)
        self.assertIn("warning-file", r.stderr)

    def test_warning_missing_file_exits_1(self):
        r = run_emit("--kind", "warning",
                     "--warning-file", "no/such/file.json")
        self.assertEqual(r.returncode, 1)

    def test_warning_file_without_message_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            wf = self._write_warning_file(
                td, {"parent": "ENG-10", "chars": 9481})
            r = run_emit("--kind", "warning", "--warning-file", str(wf))
            self.assertEqual(r.returncode, 1)
            self.assertIn("message", r.stderr)

    # ---------- emitted JSON parses cleanly ----------

    def test_emitted_json_is_valid_for_every_kind(self):
        cases = [
            ("state attempt", ("--kind", "state", "--state", "implement",
                               "--attempt", "1",
                               "--started-at", "2026-05-26T12:00:00Z")),
            ("state failure", ("--kind", "state", "--state", "implement",
                               "--attempt", "1", "--status", "failed",
                               "--error", "boom")),
            ("gate waiting", ("--kind", "gate", "--state", "plan_review",
                              "--status", "waiting")),
            ("gate rework", ("--kind", "gate", "--state", "plan_review",
                             "--status", "rework", "--rework-to", "plan")),
            ("gate escalated", ("--kind", "gate", "--state", "human_review",
                                "--status", "escalated")),
            ("reconcile", ("--kind", "reconcile",
                           "--observed-linear-state", "Done",
                           "--expected-state", "implement",
                           "--reason", "human moved")),
            ("sweep", ("--kind", "sweep",
                       "--cleared-at", "2026-05-26T12:00:00Z",
                       "--last-activity", "2026-05-26T11:00:00Z",
                       "--stale-minutes", "60",
                       "--threshold-minutes", "30")),
            ("merge merged", ("--kind", "merge", "--state", "human_review",
                              "--status", "merged",
                              "--pr-url", "https://github.com/o/r/pull/7")),
            ("merge already_merged", ("--kind", "merge", "--state",
                                      "human_review", "--status",
                                      "already_merged", "--pr-url",
                                      "https://github.com/o/r/pull/7")),
            ("merge failed", ("--kind", "merge", "--state", "human_review",
                              "--status", "failed", "--error", "boom")),
            ("merge no_pr", ("--kind", "merge", "--state", "human_review",
                             "--status", "no_pr")),
        ]
        for name, args in cases:
            with self.subTest(case=name):
                r = run_emit(*args)
                self.assertEqual(r.returncode, 0, msg=r.stderr)
                m = BODY_RE.match(r.stdout.rstrip("\n"))
                self.assertIsNotNone(m, f"body did not match: {r.stdout!r}")
                # Strict re-parse — must round-trip.
                self.assertIsInstance(json.loads(m.group("json")), dict)


if __name__ == "__main__":
    unittest.main()
