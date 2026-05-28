"""Tests for templates/hooks/render_status_report.py.

Covers header rendering, issue-table rendering (incl empty-set sentinel),
workflow-state column lookup (pickup / state / gate_waiting), verdict
column logic, per-state summary (agent / gate four-bucket breakdown /
terminal / pickup), Concurrency section (omit / AT CAP / OVER CAP),
Config warnings (validator FAIL evidence + degraded fetches), and
priority+updatedAt sorting.

Three byte-identical fixture comparisons cover the broad-stroke acceptance
criteria (AC-1, AC-2, AC-3) — these guard against any future edit to the
renderer that changes the visible report shape.

The validator-output dict is built directly in-test rather than running
validate_workflow.py, so the matrix can vary state config (custom caps,
adversarial flags, evidence FAIL blocks) without composing YAML.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "hooks" / "render_status_report.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "status"


def _default_validator_output(caps=None, evidence=None):
    """Build a validator-output dict for the default README workflow.

    caps is {state_name: int} to attach max_in_flight to states.
    evidence, if provided, is appended verbatim under the "evidence" key
    (so Config-warnings tests can hand-craft FAIL blocks).
    """
    caps = caps or {}
    linear = {
        "team": "ENG",
        "pickup_state": "Todo",
        "project_slug": "cadence",
    }
    states = {
        "plan": {
            "type": "agent", "subagent": "planner",
            "linear_state": "Planning", "next": "plan_review",
        },
        "plan_review": {
            "type": "gate", "linear_state": "Plan Review",
            "on_approve": "implement", "on_rework": "plan",
        },
        "implement": {
            "type": "agent", "subagent": "implementer",
            "linear_state": "Implementing", "next": "agent_review",
        },
        "agent_review": {
            "type": "agent", "subagent": "reviewer",
            "linear_state": "Reviewing",
            "adversarial_context": True, "next": "human_review",
        },
        "human_review": {
            "type": "gate", "linear_state": "In Review",
            "on_approve": "done", "on_rework": "implement",
        },
        "done": {"type": "terminal", "linear_state": "Done"},
    }
    for name, cap in caps.items():
        states[name]["max_in_flight"] = cap
    linear_to_workflow = {
        "Todo": {"kind": "pickup", "workflow_state": None,
                 "linear_state_type": None},
    }
    for name, body in states.items():
        kind = "gate_waiting" if body["type"] == "gate" else "state"
        linear_to_workflow[body["linear_state"]] = {
            "kind": kind, "workflow_state": name,
            "linear_state_type": body["type"],
        }
    out = {
        "valid": True,
        "entry_state_name": "plan",
        "entry_subagent": "planner",
        "workflow_linear_states": list(linear_to_workflow.keys()),
        "linear_to_workflow": linear_to_workflow,
        "pickup_state": "Todo",
        "states": states,
        "linear": linear,
        "label": {
            "cadence_active": "cadence-active",
            "cadence_needs_human": "cadence-needs-human",
            "cadence_approve": "cadence-approve",
            "cadence_rework": "cadence-rework",
        },
        "limits": {"max_attempts_per_issue": 3},
    }
    if evidence is not None:
        out["evidence"] = evidence
        out["valid"] = False
    return out


def _issue(identifier, *, column, title="A title", priority=2,
           updated_at="2026-05-28T11:00:00Z", labels=None,
           attempt_count=1, last_state=None):
    return {
        "identifier": identifier,
        "title": title,
        "state_name": column,
        "priority": priority,
        "updatedAt": updated_at,
        "labels": labels or [],
        "attempt_count": attempt_count,
        "last_state": last_state,
    }


def _run(payload, td):
    input_path = td / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(input_path)],
        cwd=str(td), capture_output=True, text=True,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Input builders for the byte-identical fixtures
# ---------------------------------------------------------------------------

def _default_full_payload():
    """One issue per workflow state, plus a pickup. Verdict labels on
    plan_review (rework) and human_review (approve)."""
    issues = [
        _issue("ENG-10", column="Todo",
               title="Pickup waiting", priority=2,
               updated_at="2026-05-28T10:00:00Z", attempt_count=0,
               last_state=None),
        _issue("ENG-11", column="Planning",
               title="Planner running",
               updated_at="2026-05-28T10:05:00Z", attempt_count=1,
               last_state="plan"),
        _issue("ENG-12", column="Plan Review",
               title="Plan needs rework", priority=3,
               updated_at="2026-05-28T10:10:00Z",
               labels=["cadence-rework"], attempt_count=1,
               last_state="plan"),
        _issue("ENG-13", column="Implementing",
               title="Implementer running", priority=2,
               updated_at="2026-05-28T10:15:00Z",
               labels=["cadence-active"], attempt_count=2,
               last_state="implement"),
        _issue("ENG-14", column="Reviewing",
               title="Adversarial review running", priority=2,
               updated_at="2026-05-28T10:20:00Z", attempt_count=1,
               last_state="agent_review"),
        _issue("ENG-15", column="In Review",
               title="Awaiting human", priority=1,
               updated_at="2026-05-28T10:25:00Z",
               labels=["cadence-approve"], attempt_count=1,
               last_state="agent_review"),
        _issue("ENG-16", column="Done",
               title="Finished issue with a much longer title that "
                     "should be truncated by the renderer to keep table "
                     "rows compact",
               priority=3,
               updated_at="2026-05-28T10:30:00Z", attempt_count=1,
               last_state="agent_review"),
    ]
    return {
        "validator": _default_validator_output(),
        "issues": issues,
        "now": "2026-05-28T12:00:00Z",
        "team": "ENG",
        "project_slug": "cadence",
        "pickup_state": "Todo",
    }


def _empty_payload():
    return {
        "validator": _default_validator_output(),
        "issues": [],
        "now": "2026-05-28T12:00:00Z",
        "team": "ENG",
        "project_slug": None,
        "pickup_state": "Todo",
    }


def _concurrency_payload():
    """One state at-cap, one over-cap. plan_review cap=1, in_flight=1.
    human_review cap=1, in_flight=2."""
    val = _default_validator_output(caps={"plan_review": 1,
                                          "human_review": 1})
    issues = [
        _issue("ENG-20", column="Plan Review", priority=2,
               updated_at="2026-05-28T10:00:00Z"),
        _issue("ENG-21", column="In Review", priority=2,
               updated_at="2026-05-28T10:05:00Z"),
        _issue("ENG-22", column="In Review", priority=2,
               updated_at="2026-05-28T10:10:00Z"),
    ]
    return {
        "validator": val,
        "issues": issues,
        "now": "2026-05-28T12:00:00Z",
        "team": "ENG",
        "project_slug": "cadence",
        "pickup_state": "Todo",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class FixtureByteIdentityTests(unittest.TestCase):

    def _assert_matches_fixture(self, payload, fixture_name):
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            expected = (FIXTURES / fixture_name).read_text(encoding="utf-8")
            self.assertEqual(r.stdout, expected,
                             msg=f"output drifted from {fixture_name}")

    def test_default_full_byte_identical(self):
        """AC-1: default workflow + one issue per state fixture."""
        self._assert_matches_fixture(_default_full_payload(),
                                     "default_full.md")

    def test_empty_byte_identical(self):
        """AC-2: empty issue set → sentinel line, no Concurrency section."""
        self._assert_matches_fixture(_empty_payload(), "empty.md")

    def test_concurrency_byte_identical(self):
        """AC-3: one at-cap state + one over-cap state."""
        self._assert_matches_fixture(_concurrency_payload(),
                                     "concurrency.md")


class HeaderTests(unittest.TestCase):

    def test_project_slug_unset_renders_any(self):
        payload = _empty_payload()
        payload["project_slug"] = None
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("Project: **(any)**", r.stdout)

    def test_project_slug_set_renders_verbatim(self):
        payload = _empty_payload()
        payload["project_slug"] = "cadence"
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("Project: **cadence**", r.stdout)

    def test_now_team_pickup_appear_in_header(self):
        payload = _empty_payload()
        payload["now"] = "2026-05-28T12:34:56Z"
        payload["team"] = "PROD"
        payload["pickup_state"] = "Backlog"
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("Cadence status — 2026-05-28T12:34:56Z", r.stdout)
            self.assertIn("Team: **PROD**", r.stdout)
            self.assertIn("Pickup: **Backlog**", r.stdout)


class IssueTableTests(unittest.TestCase):

    def test_empty_issue_set_emits_sentinel(self):
        payload = _empty_payload()
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("*No issues currently in workflow states.*",
                          r.stdout)
            self.assertNotIn("| ID | Title |", r.stdout)

    def test_pickup_workflow_state_column(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Todo", attempt_count=0)]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # Pickup workflow-state cell, attempt 0 rendered as "—"
            self.assertIn("(pickup)", r.stdout)
            self.assertIn("ENG-1", r.stdout)

    def test_agent_state_workflow_column_renders_state_name(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Implementing", attempt_count=2)]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # The Workflow-state cell is "implement" (the workflow state
            # name), not "Implementing" (the Linear column).
            self.assertRegex(r.stdout,
                             r"\|\s*Implementing\s*\|\s*implement\s*\|")

    def test_gate_waiting_workflow_column(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="In Review", attempt_count=1)]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("human_review (waiting)", r.stdout)

    def test_lock_glyph_when_cadence_active(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Implementing",
                   labels=["cadence-active"])]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("\U0001F512", r.stdout)

    def test_needs_human_glyph_when_label_present(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Implementing",
                   labels=["cadence-needs-human"])]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("\U0001F6D1", r.stdout)

    def test_verdict_approve_only(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="In Review",
                   labels=["cadence-approve"])]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertRegex(r.stdout, r"\|\s*cadence-approve\s*\|")

    def test_verdict_rework_only(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Plan Review",
                   labels=["cadence-rework"])]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertRegex(r.stdout, r"\|\s*cadence-rework\s*\|")

    def test_verdict_both_labels(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="In Review",
                   labels=["cadence-approve", "cadence-rework"])]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("both (→ rework)", r.stdout)

    def test_verdict_empty_for_non_gate_row(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Implementing",
                   labels=["cadence-approve"])]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # Approve label on a non-gate row is not surfaced as a verdict
            # cell. The cell should be empty (last cell before the
            # newline is verdict).
            row_lines = [
                line for line in r.stdout.splitlines()
                if line.startswith("| ENG-1 ")
            ]
            self.assertEqual(len(row_lines), 1)
            cells = row_lines[0].split("|")
            self.assertEqual(cells[-2].strip(), "")

    def test_title_truncation_at_50_chars(self):
        payload = _empty_payload()
        long_title = "A" * 80
        payload["issues"] = [
            _issue("ENG-1", column="Implementing", title=long_title)]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("…", r.stdout)  # ellipsis present
            # The truncated cell should contain at most 50 characters
            # (49 A's + ellipsis).
            self.assertIn("A" * 49 + "…", r.stdout)
            self.assertNotIn("A" * 80, r.stdout)

    def test_newlines_in_title_collapse_to_spaces(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Implementing",
                   title="First line\nSecond line")]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("First line Second line", r.stdout)

    def test_pipe_in_title_is_escaped(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Implementing",
                   title="Has | a pipe")]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("Has \\| a pipe", r.stdout)

    def test_attempt_zero_renders_dash(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Todo", attempt_count=0)]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # Find the ENG-1 row and check the attempt column.
            row = [
                line for line in r.stdout.splitlines()
                if line.startswith("| ENG-1 ")
            ][0]
            cells = [c.strip() for c in row.split("|")]
            self.assertEqual(cells[5], "—")

    def test_attempt_question_mark_passes_through(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Implementing", attempt_count="?")]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            row = [
                line for line in r.stdout.splitlines()
                if line.startswith("| ENG-1 ")
            ][0]
            cells = [c.strip() for c in row.split("|")]
            self.assertEqual(cells[5], "?")


class SortOrderTests(unittest.TestCase):

    def test_priority_ascending_with_null_last(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-NULL", column="Implementing", priority=None,
                   updated_at="2026-05-28T11:00:00Z"),
            _issue("ENG-LOW", column="Implementing", priority=4,
                   updated_at="2026-05-28T11:00:00Z"),
            _issue("ENG-URG", column="Implementing", priority=1,
                   updated_at="2026-05-28T11:00:00Z"),
        ]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            urg_idx = r.stdout.index("ENG-URG")
            low_idx = r.stdout.index("ENG-LOW")
            null_idx = r.stdout.index("ENG-NULL")
            self.assertLess(urg_idx, low_idx)
            self.assertLess(low_idx, null_idx)

    def test_updated_at_descending_within_priority(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-OLD", column="Implementing", priority=2,
                   updated_at="2026-05-01T00:00:00Z"),
            _issue("ENG-NEW", column="Implementing", priority=2,
                   updated_at="2026-05-28T00:00:00Z"),
        ]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            new_idx = r.stdout.index("ENG-NEW")
            old_idx = r.stdout.index("ENG-OLD")
            self.assertLess(new_idx, old_idx)


class PerStateSummaryTests(unittest.TestCase):

    def test_terminal_state_included_when_zero(self):
        payload = _empty_payload()
        payload["issues"] = []
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("**done** (`Done`) — 0 issues", r.stdout)

    def test_agent_state_lock_and_needs_human_counts(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Implementing",
                   labels=["cadence-active"]),
            _issue("ENG-2", column="Implementing",
                   labels=["cadence-needs-human"]),
            _issue("ENG-3", column="Implementing"),
        ]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            line = [
                ln for ln in r.stdout.splitlines()
                if ln.startswith("- **implement**")
            ][0]
            self.assertIn("— 3 issues", line)
            self.assertIn("\U0001F512 1 locked", line)
            self.assertIn("\U0001F6D1 1 needs-human", line)

    def test_gate_collapses_to_single_line_when_all_awaiting(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="In Review"),
            _issue("ENG-2", column="In Review"),
        ]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn(
                "- **human_review** (gate, `In Review`) — 2 issues",
                r.stdout)
            self.assertNotIn("awaiting verdict", r.stdout)

    def test_gate_breakdown_omits_zero_count_lines(self):
        """Mix of awaiting + approve, no rework / both → only those two
        sub-bullets render."""
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="In Review"),
            _issue("ENG-2", column="In Review",
                   labels=["cadence-approve"]),
        ]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("awaiting verdict — 1 issues", r.stdout)
            self.assertIn("\U0001F44D cadence-approve — 1 issues", r.stdout)
            self.assertNotIn("cadence-rework — ", r.stdout)
            self.assertNotIn("both labels", r.stdout)

    def test_gate_all_four_buckets_render(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="In Review"),
            _issue("ENG-2", column="In Review",
                   labels=["cadence-approve"]),
            _issue("ENG-3", column="In Review",
                   labels=["cadence-rework"]),
            _issue("ENG-4", column="In Review",
                   labels=["cadence-approve", "cadence-rework"]),
        ]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("awaiting verdict — 1 issues", r.stdout)
            self.assertIn("\U0001F44D cadence-approve — 1 issues", r.stdout)
            self.assertIn("\U0001F44E cadence-rework — 1 issues", r.stdout)
            self.assertIn("⚠️ both labels (treated as rework) — 1 issues",
                          r.stdout)

    def test_pickup_line_appears_after_states(self):
        payload = _empty_payload()
        payload["issues"] = [
            _issue("ENG-1", column="Todo"),
            _issue("ENG-2", column="Todo"),
        ]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("- **(pickup)** (`Todo`) — 2 issues", r.stdout)


class ConcurrencyTests(unittest.TestCase):

    def test_omitted_when_no_max_in_flight(self):
        payload = _empty_payload()
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertNotIn("### Concurrency", r.stdout)

    def test_appears_when_any_cap_declared(self):
        payload = _empty_payload()
        payload["validator"] = _default_validator_output(
            caps={"plan_review": 5})
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("### Concurrency", r.stdout)

    def test_at_cap_label_when_inflight_equals_cap(self):
        payload = _empty_payload()
        payload["validator"] = _default_validator_output(
            caps={"plan_review": 1})
        payload["issues"] = [_issue("ENG-1", column="Plan Review")]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("AT CAP", r.stdout)
            self.assertNotIn("OVER CAP", r.stdout)

    def test_over_cap_label_when_inflight_exceeds_cap(self):
        payload = _empty_payload()
        payload["validator"] = _default_validator_output(
            caps={"plan_review": 1})
        payload["issues"] = [
            _issue("ENG-1", column="Plan Review"),
            _issue("ENG-2", column="Plan Review"),
        ]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("OVER CAP", r.stdout)

    def test_terminal_state_cap_cell_is_na(self):
        payload = _empty_payload()
        payload["validator"] = _default_validator_output(
            caps={"plan_review": 1})
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # Find the "done (terminal)" row.
            done_row = [
                line for line in r.stdout.splitlines()
                if "done (terminal)" in line
            ]
            self.assertEqual(len(done_row), 1)
            self.assertIn("n/a", done_row[0])

    def test_gate_and_terminal_markers_in_state_column(self):
        payload = _empty_payload()
        payload["validator"] = _default_validator_output(
            caps={"plan_review": 1})
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("plan_review (gate)", r.stdout)
            self.assertIn("human_review (gate)", r.stdout)
            self.assertIn("done (terminal)", r.stdout)


class ConfigWarningsTests(unittest.TestCase):

    def test_validator_failures_render_rule_title_and_failure(self):
        evidence = [{
            "rule": 1, "title": "Linear-state uniqueness",
            "lines": [], "result": "FAIL",
            "failure": ('linear.pickup_state and states.plan.linear_state '
                        'both = "Todo"'),
        }, {
            "rule": 3, "title": "Targets", "lines": [],
            "result": "PASS", "failure": None,
        }]
        payload = _empty_payload()
        payload["validator"] = _default_validator_output(evidence=evidence)
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("### Config warnings", r.stdout)
            self.assertIn("Rule 1 (Linear-state uniqueness)", r.stdout)
            self.assertIn("both = \"Todo\"", r.stdout)
            # PASS rules are NOT surfaced.
            self.assertNotIn("Rule 3", r.stdout)

    def test_degraded_issues_surfaced(self):
        payload = _empty_payload()
        payload["degraded_issues"] = ["ENG-1", "ENG-2"]
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("### Config warnings", r.stdout)
            self.assertIn("ENG-1, ENG-2", r.stdout)

    def test_no_warnings_section_when_clean(self):
        payload = _empty_payload()
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertNotIn("### Config warnings", r.stdout)


class FooterTests(unittest.TestCase):

    def test_footer_always_present(self):
        payload = _empty_payload()
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertTrue(
                r.stdout.rstrip().endswith(
                    "Read-only — no Linear writes performed."),
                msg=f"footer missing in:\n{r.stdout!r}")


class CliErrorTests(unittest.TestCase):

    def test_missing_input_arg_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=td, capture_output=True, text=True,
                encoding="utf-8",
            )
            self.assertNotEqual(r.returncode, 0)

    def test_unreadable_input_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--input", str(td / "missing.json")],
                cwd=str(td), capture_output=True, text=True,
                encoding="utf-8",
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("Cadence", r.stderr)

    def test_non_object_input_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            p = td / "input.json"
            p.write_text("[1, 2, 3]", encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(p)],
                cwd=str(td), capture_output=True, text=True,
                encoding="utf-8",
            )
            self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
