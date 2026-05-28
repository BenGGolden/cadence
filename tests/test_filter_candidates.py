"""Tests for templates/hooks/filter_candidates.py.

Covers plan mode (queries the prose should fire) and filter mode (post-
query candidate filter, priority sort, bounded reachability walk, drain
exemption, diagnostic-message rendering). Invoked via subprocess so the
full _load_json -> _build_plan / _filter -> main() path is exercised on
every run.

The fixture builds a `validator output`-shaped dict directly rather than
running validate_workflow.py, so the test matrix can vary state config
freely (over-cap on a gate downstream of the entry, custom on_rework
targets, etc.) without composing a separate YAML each time.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "hooks" / "filter_candidates.py"


def _default_validator_output(project_slug=None, caps=None,
                              on_rework=None):
    """A validator-output dict shaped like the JSON validate_workflow.py
    prints, for the default workflow shape used in the README.

    caps is {state_name: int} to attach max_in_flight to states.
    on_rework is {gate_name: target} to override the default
    plan_review/human_review on_rework targets (handy for the AC-5
    rework-walk case).
    """
    caps = caps or {}
    on_rework_default = {
        "plan_review": "plan",
        "human_review": "implement",
    }
    on_rework_default.update(on_rework or {})
    linear = {"team": "ENG", "pickup_state": "Todo"}
    if project_slug is not None:
        linear["project_slug"] = project_slug
    states = {
        "plan": {
            "type": "agent", "subagent": "planner",
            "linear_state": "Planning", "next": "plan_review",
        },
        "plan_review": {
            "type": "gate", "linear_state": "Plan Review",
            "on_approve": "implement",
            "on_rework": on_rework_default["plan_review"],
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
            "on_approve": "done",
            "on_rework": on_rework_default["human_review"],
        },
        "done": {"type": "terminal", "linear_state": "Done"},
    }
    for name, cap in caps.items():
        states[name]["max_in_flight"] = cap
    workflow_linear_states = ["Todo", "Planning", "Plan Review",
                              "Implementing", "Reviewing", "In Review",
                              "Done"]
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
    return {
        "valid": True,
        "entry_state_name": "plan",
        "entry_subagent": "planner",
        "workflow_linear_states": workflow_linear_states,
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


def _write_json(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def _run(args, cwd):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        cwd=str(cwd), capture_output=True, text=True,
    )


def _candidate(identifier, *, column, labels=None, priority=2,
               created_at="2026-05-01T00:00:00Z", blockers=None):
    c = {
        "identifier": identifier,
        "current_linear_state": column,
        "labels": labels or [],
        "priority": priority,
        "createdAt": created_at,
    }
    if blockers is not None:
        c["blockers"] = blockers
    return c


class PlanModeTests(unittest.TestCase):

    def test_no_caps_emits_empty_in_flight_queries(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "cfg.json"
            _write_json(cfg, _default_validator_output())
            r = _run(["--plan", "--workflow-config", str(cfg)], td)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["in_flight_queries"], [])
            self.assertEqual(out["pickup_query"]["team"], "ENG")
            self.assertEqual(out["pickup_query"]["workflow_linear_states"][0],
                             "Todo")

    def test_caps_on_agent_and_gate_both_surface(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "cfg.json"
            wf = _default_validator_output(
                caps={"plan_review": 5, "implement": 3})
            _write_json(cfg, wf)
            r = _run(["--plan", "--workflow-config", str(cfg)], td)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            entries = {e["state_name"]: e["linear_state"]
                       for e in out["in_flight_queries"]}
            self.assertEqual(entries,
                             {"plan_review": "Plan Review",
                              "implement": "Implementing"})

    def test_default_workflow_in_flight_queries_match_max_in_flight_states(self):
        """AC-1: every state with max_in_flight surfaces, each with its
        correct linear_state, and no others."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "cfg.json"
            wf = _default_validator_output(
                caps={"plan": 2, "human_review": 5})
            _write_json(cfg, wf)
            r = _run(["--plan", "--workflow-config", str(cfg)], td)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            entries = [(e["state_name"], e["linear_state"])
                       for e in out["in_flight_queries"]]
            self.assertEqual(set(entries),
                             {("plan", "Planning"),
                              ("human_review", "In Review")})
            self.assertEqual(len(entries), 2)

    def test_project_slug_absent_is_json_null(self):
        """AC-2: pickup_query.project_slug is JSON null (not empty string)
        when the config omits linear.project_slug."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "cfg.json"
            _write_json(cfg, _default_validator_output(project_slug=None))
            r = _run(["--plan", "--workflow-config", str(cfg)], td)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn('"project_slug": null', r.stdout)
            out = json.loads(r.stdout)
            self.assertIsNone(out["pickup_query"]["project_slug"])

    def test_project_slug_empty_string_is_json_null(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "cfg.json"
            _write_json(cfg, _default_validator_output(project_slug=""))
            r = _run(["--plan", "--workflow-config", str(cfg)], td)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertIsNone(out["pickup_query"]["project_slug"])

    def test_project_slug_present_passes_through(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "cfg.json"
            _write_json(cfg, _default_validator_output(project_slug="cadence"))
            r = _run(["--plan", "--workflow-config", str(cfg)], td)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["pickup_query"]["project_slug"], "cadence")


class FilterModeBasicsTests(unittest.TestCase):

    def _run_filter(self, td, wf, candidates, in_flight):
        cfg = td / "cfg.json"
        cand = td / "candidates.json"
        infl = td / "in_flight.json"
        _write_json(cfg, wf)
        _write_json(cand, candidates)
        _write_json(infl, in_flight)
        r = _run(["--workflow-config", str(cfg),
                  "--candidates", str(cand),
                  "--in-flight", str(infl)], td)
        return r

    def test_empty_candidates_no_caps_blocked(self):
        """Filter mode: empty candidate list → bare 'No eligible issues.'
        with no parenthetical (no caps blocked anything)."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = self._run_filter(td, _default_validator_output(), [], {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])
            self.assertEqual(out["over_cap_states_that_blocked"], [])
            self.assertEqual(out["diagnostic_message"], "No eligible issues.")

    def test_cadence_active_label_drops_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Todo",
                                labels=["cadence-active"])]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])
            self.assertEqual(out["diagnostic_message"], "No eligible issues.")

    def test_cadence_needs_human_label_drops_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Todo",
                                labels=[{"name": "cadence-needs-human"}])]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])

    def test_foreign_linear_column_drops_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Backlog")]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])

    def test_terminal_column_drops_candidate(self):
        """Issues sitting in a terminal-type state's Linear column ("Done"
        in the default workflow) are not picked up. The workflow is
        complete for them; step 14 would have no subagent to invoke."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-DONE", column="Done")]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])
            self.assertEqual(out["diagnostic_message"], "No eligible issues.")

    def test_terminal_drop_does_not_apply_to_pickup_state(self):
        """The pickup state ("Todo") is not terminal — issues there must
        still be picked up (that's the entry path into the workflow)."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Todo")]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])

    def test_blockers_absent_is_skipped(self):
        """blockers field absent → filter not applied; candidate passes."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Todo")]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])

    def test_blockers_present_unresolved_drops_candidate(self):
        """blockers in workflow linear states (e.g. "Implementing") =>
        candidate dropped."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Todo",
                                blockers=["Implementing"])]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])

    def test_blockers_present_resolved_passes_candidate(self):
        """blockers in foreign columns (e.g. "Cancelled") => candidate
        not blocked."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Todo",
                                blockers=["Cancelled"])]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])

    def test_gate_waiting_without_verdict_dropped(self):
        """Issue in a gate's waiting column with neither verdict label =>
        dropped (the bootstrap can't do anything for it until a human acts)."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Plan Review")]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])

    def test_gate_waiting_with_approve_passes(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Plan Review",
                                labels=["cadence-approve"])]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])

    def test_gate_waiting_with_rework_passes(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [_candidate("ENG-1", column="Plan Review",
                                labels=["cadence-rework"])]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])


class FilterModeSortAndCapTests(unittest.TestCase):

    def _run_filter(self, td, wf, candidates, in_flight):
        cfg = td / "cfg.json"
        cand = td / "candidates.json"
        infl = td / "in_flight.json"
        _write_json(cfg, wf)
        _write_json(cand, candidates)
        _write_json(infl, in_flight)
        r = _run(["--workflow-config", str(cfg),
                  "--candidates", str(cand),
                  "--in-flight", str(infl)], td)
        return r

    def test_priority_sort_high_first(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [
                _candidate("ENG-LOW", column="Todo", priority=4),
                _candidate("ENG-URG", column="Todo", priority=1),
                _candidate("ENG-MED", column="Todo", priority=3),
            ]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"],
                             ["ENG-URG", "ENG-MED", "ENG-LOW"])

    def test_null_and_zero_priority_sort_last(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [
                _candidate("ENG-NULL", column="Todo", priority=None,
                           created_at="2026-05-01T00:00:00Z"),
                _candidate("ENG-NONE", column="Todo", priority=0,
                           created_at="2026-05-02T00:00:00Z"),
                _candidate("ENG-MED", column="Todo", priority=3,
                           created_at="2026-05-03T00:00:00Z"),
            ]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"][0], "ENG-MED")
            self.assertEqual(set(out["ordered_identifiers"][1:]),
                             {"ENG-NULL", "ENG-NONE"})

    def test_created_at_breaks_priority_tie(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [
                _candidate("ENG-B", column="Todo", priority=2,
                           created_at="2026-05-02T00:00:00Z"),
                _candidate("ENG-A", column="Todo", priority=2,
                           created_at="2026-05-01T00:00:00Z"),
            ]
            r = self._run_filter(td, _default_validator_output(), cands, {})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-A", "ENG-B"])

    def test_stable_sort_on_total_ties_across_repeated_runs(self):
        """AC-6: two candidates with identical priority and createdAt
        appear in input order across 10 runs."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [
                _candidate("ENG-FIRST", column="Todo", priority=2,
                           created_at="2026-05-01T00:00:00Z"),
                _candidate("ENG-SECOND", column="Todo", priority=2,
                           created_at="2026-05-01T00:00:00Z"),
            ]
            for _ in range(10):
                r = self._run_filter(td, _default_validator_output(),
                                     cands, {})
                self.assertEqual(r.returncode, 0, msg=r.stderr)
                out = json.loads(r.stdout)
                self.assertEqual(out["ordered_identifiers"],
                                 ["ENG-FIRST", "ENG-SECOND"])

    def test_ac3_todo_blocked_by_plan_review_cap(self):
        """AC-3: default workflow + one Todo candidate + plan_review at cap
        → empty ordered list, plan_review reported as blocker, diagnostic
        message includes the parenthetical."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(caps={"plan_review": 1})
            cands = [_candidate("ENG-1", column="Todo")]
            r = self._run_filter(td, wf, cands, {"plan_review": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])
            self.assertEqual(out["over_cap_states_that_blocked"],
                             ["plan_review"])
            self.assertEqual(out["diagnostic_message"],
                             "No eligible issues.\n"
                             "(caps reached for: plan_review)")

    def test_ac4_drain_exemption_at_own_gate(self):
        """AC-4: verdict-bearing candidate at human_review with
        cadence_approve, human_review at cap → candidate IS in
        ordered_identifiers (drain exemption)."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(caps={"human_review": 1})
            cands = [_candidate("ENG-1", column="In Review",
                                labels=["cadence-approve"])]
            r = self._run_filter(td, wf, cands, {"human_review": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])
            self.assertEqual(out["over_cap_states_that_blocked"], [])

    def test_ac5_rework_walk_blocked_by_downstream_human_review_cap(self):
        """AC-5: plan_review on_rework: implement (custom). Candidate at
        plan_review with cadence_rework → walk = implement → agent_review
        → human_review. human_review at cap → candidate dropped,
        human_review in blocker list."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(
                caps={"human_review": 1},
                on_rework={"plan_review": "implement"})
            cands = [_candidate("ENG-1", column="Plan Review",
                                labels=["cadence-rework"])]
            r = self._run_filter(td, wf, cands, {"human_review": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])
            self.assertEqual(out["over_cap_states_that_blocked"],
                             ["human_review"])

    def test_walk_bounded_at_first_gate_so_downstream_cap_not_seen(self):
        """A Todo candidate's walk stops at plan_review. A cap on
        implement (past the boundary) must NOT affect the candidate."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(caps={"implement": 1})
            cands = [_candidate("ENG-1", column="Todo")]
            r = self._run_filter(td, wf, cands, {"implement": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])
            self.assertEqual(out["over_cap_states_that_blocked"], [])

    def test_walk_for_pickup_candidate_blocked_by_entry_state_cap(self):
        """Todo candidate's walk = plan → plan_review. Cap on plan binds."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(caps={"plan": 1})
            cands = [_candidate("ENG-1", column="Todo")]
            r = self._run_filter(td, wf, cands, {"plan": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])
            self.assertEqual(out["over_cap_states_that_blocked"], ["plan"])

    def test_over_cap_state_not_on_walk_is_not_reported(self):
        """A state can be over-cap without blocking anything if no
        candidate's walk passes through it."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(caps={"agent_review": 1})
            cands = [_candidate("ENG-1", column="Todo")]
            r = self._run_filter(td, wf, cands, {"agent_review": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])
            self.assertEqual(out["over_cap_states_that_blocked"], [])

    def test_drain_exemption_only_excludes_own_gate(self):
        """A verdict-bearing candidate at human_review with cadence_rework
        (walk = implement → agent_review → human_review). human_review is
        drain-exempt for this candidate, but a cap on agent_review still
        blocks."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(
                caps={"human_review": 1, "agent_review": 1})
            cands = [_candidate("ENG-1", column="In Review",
                                labels=["cadence-rework"])]
            r = self._run_filter(td, wf, cands,
                                 {"human_review": 1, "agent_review": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])
            self.assertEqual(out["over_cap_states_that_blocked"],
                             ["agent_review"])

    def test_both_verdict_labels_routed_as_rework(self):
        """tick.md "Both verdict labels present" semantics: treat as rework.
        With caps on agent_review (downstream of rework walk
        implement → agent_review → human_review) and both labels, the
        candidate should be dropped."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(
                caps={"agent_review": 1},
                on_rework={"human_review": "implement"})
            cands = [_candidate("ENG-1", column="In Review",
                                labels=["cadence-approve", "cadence-rework"])]
            r = self._run_filter(td, wf, cands, {"agent_review": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])
            self.assertIn("agent_review", out["over_cap_states_that_blocked"])

    def test_terminal_target_walk_has_no_caps(self):
        """human_review with cadence_approve targets done (terminal).
        Walk = [done]; no caps bind (no candidates blocked even when
        every agent state is at cap)."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(
                caps={"plan": 1, "implement": 1, "agent_review": 1})
            cands = [_candidate("ENG-1", column="In Review",
                                labels=["cadence-approve"])]
            r = self._run_filter(td, wf, cands,
                                 {"plan": 1, "implement": 1,
                                  "agent_review": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])

    def test_diagnostic_lists_multiple_blocking_states(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(
                caps={"plan_review": 1, "human_review": 1})
            cands = [
                _candidate("ENG-1", column="Todo"),
                _candidate("ENG-2", column="Plan Review",
                           labels=["cadence-approve"]),
            ]
            r = self._run_filter(td, wf, cands,
                                 {"plan_review": 1, "human_review": 1})
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])
            blocked = set(out["over_cap_states_that_blocked"])
            self.assertEqual(blocked, {"plan_review", "human_review"})
            self.assertTrue(out["diagnostic_message"].startswith(
                "No eligible issues.\n(caps reached for: "))


class FilterModeMiscTests(unittest.TestCase):

    def test_under_cap_count_does_not_block(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _default_validator_output(caps={"plan_review": 3})
            cands = [_candidate("ENG-1", column="Todo")]
            cfg = td / "cfg.json"
            cand = td / "candidates.json"
            infl = td / "in_flight.json"
            _write_json(cfg, wf)
            _write_json(cand, cands)
            _write_json(infl, {"plan_review": 1})
            r = _run(["--workflow-config", str(cfg),
                      "--candidates", str(cand),
                      "--in-flight", str(infl)], td)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], ["ENG-1"])

    def test_label_nodes_shape_tolerated(self):
        """Labels in the GraphQL connection shape ({"nodes": [...]}) are
        treated like a flat list of label dicts."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cands = [{
                "identifier": "ENG-1",
                "current_linear_state": "Todo",
                "labels": {"nodes": [{"name": "cadence-active"}]},
                "priority": 2,
                "createdAt": "2026-05-01T00:00:00Z",
            }]
            cfg = td / "cfg.json"
            cand = td / "candidates.json"
            infl = td / "in_flight.json"
            _write_json(cfg, _default_validator_output())
            _write_json(cand, cands)
            _write_json(infl, {})
            r = _run(["--workflow-config", str(cfg),
                      "--candidates", str(cand),
                      "--in-flight", str(infl)], td)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["ordered_identifiers"], [])


class CliErrorTests(unittest.TestCase):

    def test_missing_workflow_config_arg(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run([], td)
            self.assertNotEqual(r.returncode, 0)

    def test_plan_mode_with_unreadable_config_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run(["--plan", "--workflow-config", str(td / "missing.json")],
                     td)
            self.assertEqual(r.returncode, 1)
            self.assertIn("Cadence", r.stderr)

    def test_filter_mode_missing_candidates_arg_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "cfg.json"
            _write_json(cfg, _default_validator_output())
            r = _run(["--workflow-config", str(cfg)], td)
            self.assertEqual(r.returncode, 1)
            self.assertIn("--candidates", r.stderr)

    def test_filter_mode_non_array_candidates_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "cfg.json"
            cand = td / "candidates.json"
            infl = td / "in_flight.json"
            _write_json(cfg, _default_validator_output())
            cand.write_text('{"not": "an array"}', encoding="utf-8")
            _write_json(infl, {})
            r = _run(["--workflow-config", str(cfg),
                      "--candidates", str(cand),
                      "--in-flight", str(infl)], td)
            self.assertEqual(r.returncode, 1)

    def test_filter_mode_non_object_in_flight_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg = td / "cfg.json"
            cand = td / "candidates.json"
            infl = td / "in_flight.json"
            _write_json(cfg, _default_validator_output())
            _write_json(cand, [])
            infl.write_text("[]", encoding="utf-8")
            r = _run(["--workflow-config", str(cfg),
                      "--candidates", str(cand),
                      "--in-flight", str(infl)], td)
            self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
