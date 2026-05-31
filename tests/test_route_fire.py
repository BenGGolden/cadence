"""Integration tests for templates/hooks/route_fire.py.

Invoked via subprocess so the full argparse + I/O + import path (parse_comments
/ classify_drift / classify_gate / emit_tracking_comment formatters) runs on
every case. This is the decision-parity matrix for the old tick.md steps 8–11:
each branch of the prose asserts the router emits the matching plan.

The fixture builds a validator-output-shaped dict directly (matching the JSON
validate_workflow.py prints) so the matrix can vary gate targets, caps, and
rework limits freely.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "hooks" / "route_fire.py"

LABELS = {
    "cadence_active": "cadence-active",
    "cadence_needs_human": "cadence-needs-human",
    "cadence_approve": "cadence-approve",
    "cadence_rework": "cadence-rework",
}


def _validator_output(*, on_rework=None, max_rework=None, max_attempts=3):
    on_rework_default = {"plan_review": "plan", "human_review": "implement"}
    on_rework_default.update(on_rework or {})
    states = {
        "plan": {"type": "agent", "subagent": "planner",
                 "linear_state": "Planning", "next": "plan_review"},
        "plan_review": {"type": "gate", "linear_state": "Plan Review",
                        "on_approve": "implement",
                        "on_rework": on_rework_default["plan_review"]},
        "implement": {"type": "agent", "subagent": "implementer",
                      "linear_state": "Implementing", "next": "agent_review"},
        "agent_review": {"type": "agent", "subagent": "reviewer",
                         "linear_state": "Reviewing",
                         "adversarial_context": True, "next": "human_review"},
        "human_review": {"type": "gate", "linear_state": "In Review",
                         "on_approve": "done",
                         "on_rework": on_rework_default["human_review"]},
        "done": {"type": "terminal", "linear_state": "Done"},
    }
    if max_rework is not None:
        states["plan_review"]["max_rework"] = max_rework
        states["human_review"]["max_rework"] = max_rework
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
        "linear_to_workflow": linear_to_workflow,
        "pickup_state": "Todo",
        "states": states,
        "linear": {"team": "ENG", "pickup_state": "Todo"},
        "label": LABELS,
        "limits": {"max_attempts_per_issue": max_attempts},
    }


def _attempt_marker(state, attempt, t, user="Alice"):
    payload = json.dumps({"state": state, "attempt": attempt, "started_at": t})
    body = (f"<!-- cadence:state {payload} -->\n"
            f"**[Cadence]** Entering state: **{state}** (attempt {attempt})")
    return {"id": f"c-{t}", "body": body, "createdAt": t,
            "user": {"displayName": user}}


def _legacy_attempt_marker(state, run, t, user="Alice"):
    payload = json.dumps({"state": state, "run": run, "timestamp": t})
    body = (f"<!-- stokowski:state {payload} -->\n"
            f"**[Stokowski]** Entering state: **{state}** (run {run})")
    return {"id": f"c-{t}", "body": body, "createdAt": t,
            "user": {"displayName": user}}


def _gate_rework(state, t, rework_to="implement", user="Alice"):
    payload = json.dumps({"state": state, "status": "rework",
                          "rework_to": rework_to})
    body = (f"<!-- cadence:gate {payload} -->\n"
            f"**[Cadence]** Rework requested; routing to **{rework_to}**.")
    return {"id": f"c-{t}", "body": body, "createdAt": t,
            "user": {"displayName": user}}


def _gate_waiting(state, t, user="Alice"):
    payload = json.dumps({"state": state, "status": "waiting"})
    body = (f"<!-- cadence:gate {payload} -->\n"
            f"**[Cadence]** Awaiting human review at **{state}**.")
    return {"id": f"c-{t}", "body": body, "createdAt": t,
            "user": {"displayName": user}}


def _run(td, wf, linear_state, comments, labels_csv=""):
    cfg = td / "cfg.json"
    com = td / "comments.json"
    cfg.write_text(json.dumps(wf), encoding="utf-8")
    com.write_text(json.dumps(comments), encoding="utf-8")
    args = [sys.executable, str(SCRIPT),
            "--workflow-config", str(cfg),
            "--linear-state", linear_state,
            "--comments", str(com),
            "--labels", labels_csv]
    r = subprocess.run(args, capture_output=True, text=True)
    return r


def _types(actions):
    return [a["type"] for a in actions]


class RouteFireTests(unittest.TestCase):

    # ---------- step 8: unmapped ----------

    def test_unmapped_column_releases(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run(td, _validator_output(), "Backlog", [])
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            plan = json.loads(r.stdout)
            self.assertFalse(plan["invoke_subagent"])
            self.assertIsNone(plan["matched_state"])
            self.assertEqual(_types(plan["exit_plan"]),
                             ["post_comment", "remove_label"])
            self.assertIn("unmapped Linear state `Backlog`",
                          plan["exit_plan"][0]["body"])
            self.assertEqual(plan["exit_plan"][1]["label"], "cadence-active")

    # ---------- agent state happy path ----------

    def test_agent_state_no_drift_invokes_subagent(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run(td, _validator_output(), "Implementing", [])
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            plan = json.loads(r.stdout)
            self.assertTrue(plan["invoke_subagent"])
            self.assertEqual(plan["matched_state"], "implement")
            self.assertEqual(plan["target_state"], "implement")
            self.assertEqual(plan["subagent"], "implementer")
            self.assertEqual(plan["attempt"], 1)
            self.assertEqual(plan["pre_actions"], [])
            self.assertFalse(plan["rework"])
            # The router parsed once; step 8 reuses this without re-parsing.
            self.assertIsInstance(plan["parse_comments_output"], dict)
            self.assertIn("rework_context", plan["parse_comments_output"])

    def test_attempt_under_cap_increments(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            comments = [
                _attempt_marker("implement", 1, "2026-05-01T00:00:00Z"),
                _attempt_marker("implement", 2, "2026-05-02T00:00:00Z"),
            ]
            r = _run(td, _validator_output(), "Implementing", comments)
            plan = json.loads(r.stdout)
            self.assertEqual(plan["attempt"], 3)
            self.assertTrue(plan["invoke_subagent"])

    def test_attempt_cap_hit_escalates(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            comments = [
                _attempt_marker("implement", 1, "2026-05-01T00:00:00Z"),
                _attempt_marker("implement", 2, "2026-05-02T00:00:00Z"),
                _attempt_marker("implement", 3, "2026-05-03T00:00:00Z"),
            ]
            r = _run(td, _validator_output(max_attempts=3), "Implementing",
                     comments)
            plan = json.loads(r.stdout)
            self.assertFalse(plan["invoke_subagent"])
            self.assertEqual(_types(plan["exit_plan"]),
                             ["post_comment", "add_label", "remove_label"])
            self.assertIn("Max attempts (`3`)", plan["exit_plan"][0]["body"])
            self.assertEqual(plan["exit_plan"][1]["label"],
                             "cadence-needs-human")
            self.assertEqual(plan["exit_plan"][2]["label"], "cadence-active")

    # ---------- drift ----------

    def test_drift_posts_reconcile_then_proceeds(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # latest tracking is `plan`; matched is `implement` (not plan.next).
            comments = [_attempt_marker("plan", 1, "2026-05-01T00:00:00Z")]
            r = _run(td, _validator_output(), "Implementing", comments)
            plan = json.loads(r.stdout)
            self.assertTrue(plan["invoke_subagent"])
            self.assertEqual(plan["target_state"], "implement")
            self.assertEqual(_types(plan["pre_actions"]), ["post_comment"])
            self.assertIn("cadence:reconcile", plan["pre_actions"][0]["body"])

    def test_drift_suppressed_by_forward_progression(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # latest `implement`; matched `agent_review` == implement.next.
            comments = [_attempt_marker("implement", 1, "2026-05-01T00:00:00Z")]
            r = _run(td, _validator_output(), "Reviewing", comments)
            plan = json.loads(r.stdout)
            self.assertTrue(plan["invoke_subagent"])
            self.assertEqual(plan["pre_actions"], [])

    def test_drift_suppressed_by_match(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            comments = [_attempt_marker("implement", 1, "2026-05-01T00:00:00Z")]
            r = _run(td, _validator_output(), "Implementing", comments)
            plan = json.loads(r.stdout)
            # attempt 2 because there's one prior implement marker, but the
            # important assertion is no reconcile pre-action.
            self.assertEqual(plan["pre_actions"], [])

    def test_drift_suppressed_by_null_latest(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run(td, _validator_output(), "Implementing", [])
            plan = json.loads(r.stdout)
            self.assertEqual(plan["pre_actions"], [])

    # ---------- gate ----------

    def test_gate_waiting_releases_no_comment(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run(td, _validator_output(), "Plan Review", [])
            plan = json.loads(r.stdout)
            self.assertFalse(plan["invoke_subagent"])
            self.assertEqual(plan["matched_state"], "plan_review")
            self.assertEqual(_types(plan["exit_plan"]), ["remove_label"])
            self.assertEqual(plan["exit_plan"][0]["label"], "cadence-active")

    def test_gate_approve_to_agent(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run(td, _validator_output(), "Plan Review", [],
                     labels_csv="cadence-approve")
            plan = json.loads(r.stdout)
            self.assertTrue(plan["invoke_subagent"])
            self.assertEqual(plan["target_state"], "implement")
            self.assertEqual(plan["subagent"], "implementer")
            self.assertEqual(_types(plan["pre_actions"]),
                             ["remove_label", "move_state"])
            self.assertEqual(plan["pre_actions"][0]["label"], "cadence-approve")
            self.assertEqual(plan["pre_actions"][1]["linear_state"],
                             "Implementing")

    def test_gate_approve_to_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run(td, _validator_output(), "In Review", [],
                     labels_csv="cadence-approve")
            plan = json.loads(r.stdout)
            self.assertFalse(plan["invoke_subagent"])
            self.assertEqual(plan["target_state"], "done")
            self.assertEqual(_types(plan["exit_plan"]),
                             ["remove_label", "move_state", "remove_label"])
            self.assertEqual(plan["exit_plan"][1]["linear_state"], "Done")
            self.assertEqual(plan["exit_plan"][2]["label"], "cadence-active")

    def test_gate_rework_under_cap(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run(td, _validator_output(), "Plan Review", [],
                     labels_csv="cadence-rework")
            plan = json.loads(r.stdout)
            self.assertTrue(plan["invoke_subagent"])
            self.assertEqual(plan["target_state"], "plan")
            self.assertEqual(plan["subagent"], "planner")
            self.assertEqual(_types(plan["pre_actions"]),
                             ["remove_label", "post_comment", "move_state"])
            self.assertEqual(plan["pre_actions"][0]["label"], "cadence-rework")
            self.assertIn("cadence:gate", plan["pre_actions"][1]["body"])
            self.assertEqual(plan["pre_actions"][2]["linear_state"], "Planning")
            self.assertTrue(plan["rework"])

    def test_gate_rework_at_cap_escalates(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # max_rework 1; one prior rework at plan_review → at cap.
            comments = [_gate_rework("plan_review", "2026-05-01T00:00:00Z",
                                     rework_to="plan")]
            r = _run(td, _validator_output(max_rework=1), "Plan Review",
                     comments, labels_csv="cadence-rework")
            plan = json.loads(r.stdout)
            self.assertFalse(plan["invoke_subagent"])
            self.assertEqual(_types(plan["exit_plan"]),
                             ["remove_label", "post_comment",
                              "add_label", "remove_label"])
            self.assertIn("escalated", plan["exit_plan"][1]["body"].lower())
            self.assertEqual(plan["exit_plan"][2]["label"],
                             "cadence-needs-human")

    def test_both_verdict_labels_treated_as_rework(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = _run(td, _validator_output(), "Plan Review", [],
                     labels_csv="cadence-approve,cadence-rework")
            plan = json.loads(r.stdout)
            self.assertEqual(plan["target_state"], "plan")
            removed = [a["label"] for a in plan["pre_actions"]
                       if a["type"] == "remove_label"]
            self.assertEqual(set(removed),
                             {"cadence-approve", "cadence-rework"})

    # ---------- double-run subsumption ----------

    def test_gate_rework_attempt_counts_against_new_target(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # plan_review on_rework: implement. Two prior implement attempts.
            # The attempt must be counted against implement (=> 3), not the
            # gate plan_review (which would be 1).
            comments = [
                _attempt_marker("implement", 1, "2026-05-01T00:00:00Z"),
                _attempt_marker("implement", 2, "2026-05-02T00:00:00Z"),
            ]
            wf = _validator_output(on_rework={"plan_review": "implement"})
            r = _run(td, wf, "Plan Review", comments,
                     labels_csv="cadence-rework")
            plan = json.loads(r.stdout)
            self.assertEqual(plan["target_state"], "implement")
            self.assertEqual(plan["attempt"], 3)

    def test_gate_rework_to_capped_target_escalates(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # Resolved target implement is already at the attempt cap → the
            # cap check (against the resolved target) escalates.
            comments = [
                _attempt_marker("implement", 1, "2026-05-01T00:00:00Z"),
                _attempt_marker("implement", 2, "2026-05-02T00:00:00Z"),
                _attempt_marker("implement", 3, "2026-05-03T00:00:00Z"),
                # Latest tracking is the gate itself → Match → no drift, so
                # this case isolates the cap escalation after gate routing.
                _gate_waiting("plan_review", "2026-05-04T00:00:00Z"),
            ]
            wf = _validator_output(on_rework={"plan_review": "implement"},
                                   max_attempts=3)
            r = _run(td, wf, "Plan Review", comments,
                     labels_csv="cadence-rework")
            plan = json.loads(r.stdout)
            self.assertFalse(plan["invoke_subagent"])
            # gate pre-actions (remove label, post rework body, move) precede
            # the cap escalation.
            self.assertEqual(
                _types(plan["exit_plan"]),
                ["remove_label", "post_comment", "move_state",
                 "post_comment", "add_label", "remove_label"])

    # ---------- legacy compatibility ----------

    def test_legacy_stokowski_routes_identically(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            legacy = [
                _legacy_attempt_marker("implement", 1, "2026-05-01T00:00:00Z"),
                _legacy_attempt_marker("implement", 2, "2026-05-02T00:00:00Z"),
            ]
            r = _run(td, _validator_output(), "Implementing", legacy)
            plan = json.loads(r.stdout)
            self.assertEqual(plan["attempt"], 3)
            self.assertTrue(plan["invoke_subagent"])

    # ---------- labels via JSON file ----------

    def test_labels_from_json_file(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            labels_file = td / "labels.json"
            labels_file.write_text(
                json.dumps([{"name": "cadence-approve"}]), encoding="utf-8")
            cfg = td / "cfg.json"
            com = td / "comments.json"
            cfg.write_text(json.dumps(_validator_output()), encoding="utf-8")
            com.write_text("[]", encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-config", str(cfg),
                 "--linear-state", "Plan Review",
                 "--comments", str(com),
                 "--labels", str(labels_file)],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            plan = json.loads(r.stdout)
            self.assertEqual(plan["target_state"], "implement")

    # ---------- determinism ----------

    def test_deterministic_byte_identical(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            comments = [_attempt_marker("implement", 1, "2026-05-01T00:00:00Z")]
            outs = set()
            for _ in range(5):
                r = _run(td, _validator_output(), "Implementing", comments)
                outs.add(r.stdout)
            self.assertEqual(len(outs), 1)


def _workflow_yaml_dict():
    """A `.claude/workflow.yaml` whose validated config matches the dict that
    `_validator_output()` builds, so the two run modes can be compared."""
    return {
        "linear": {"team": "ENG", "pickup_state": "Todo"},
        "label": LABELS,
        "limits": {"max_attempts_per_issue": 3},
        "entry": "plan",
        "states": {
            "plan": {"type": "agent", "subagent": "planner",
                     "linear_state": "Planning", "next": "plan_review"},
            "plan_review": {"type": "gate", "linear_state": "Plan Review",
                            "on_approve": "implement", "on_rework": "plan"},
            "implement": {"type": "agent", "subagent": "implementer",
                          "linear_state": "Implementing", "next": "agent_review"},
            "agent_review": {"type": "agent", "subagent": "reviewer",
                             "linear_state": "Reviewing",
                             "adversarial_context": True,
                             "next": "human_review"},
            "human_review": {"type": "gate", "linear_state": "In Review",
                             "on_approve": "done", "on_rework": "implement"},
            "done": {"type": "terminal", "linear_state": "Done"},
        },
    }


def _materialise_workflow(td, wf):
    """Write `.claude/workflow.yaml` + the three agent files under td."""
    claude = td / ".claude"
    agents = claude / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    for a in ("planner", "implementer", "reviewer"):
        (agents / f"{a}.md").write_text("# agent\n", encoding="utf-8")
    wf_path = claude / "workflow.yaml"
    wf_path.write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
    return wf_path


class RouteFireWorkflowPathTests(unittest.TestCase):
    """--workflow-path mode: validate .claude/workflow.yaml internally."""

    def test_parity_with_workflow_config(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            comments = [_attempt_marker("implement", 1, "2026-05-01T00:00:00Z")]
            cfg_plan = json.loads(
                _run(td, _validator_output(), "Implementing", comments).stdout)

            wf_path = _materialise_workflow(td, _workflow_yaml_dict())
            com = td / "comments.json"
            com.write_text(json.dumps(comments), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-path", str(wf_path),
                 "--linear-state", "Implementing",
                 "--comments", str(com),
                 "--labels", ""],
                cwd=str(td), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(json.loads(r.stdout), cfg_plan)

    def test_invalid_workflow_bails_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _workflow_yaml_dict()
            wf["states"]["implement"]["next"] = "nonexistent"
            wf_path = _materialise_workflow(td, wf)
            com = td / "comments.json"
            com.write_text("[]", encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-path", str(wf_path),
                 "--linear-state", "Implementing",
                 "--comments", str(com),
                 "--labels", ""],
                cwd=str(td), capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn("Rule 3 (Targets) FAILED", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_workflow_config_wins_when_both_passed(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # A broken YAML on disk; --workflow-config should be used instead.
            wf = _workflow_yaml_dict()
            wf["states"]["implement"]["next"] = "nonexistent"
            wf_path = _materialise_workflow(td, wf)
            cfg = td / "cfg.json"
            cfg.write_text(json.dumps(_validator_output()), encoding="utf-8")
            com = td / "comments.json"
            com.write_text("[]", encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-config", str(cfg),
                 "--workflow-path", str(wf_path),
                 "--linear-state", "Implementing",
                 "--comments", str(com),
                 "--labels", ""],
                cwd=str(td), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            plan = json.loads(r.stdout)
            self.assertEqual(plan["target_state"], "implement")


if __name__ == "__main__":
    unittest.main()
