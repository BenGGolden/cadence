"""Tests for templates/hooks/validate_workflow.py.

Covers rules 1-8 (pass + fail paths), --evidence output shape, exit codes,
and workflow_linear_states ordering. Invoked via subprocess so the full
load_workflow -> rules -> main() path is exercised on every run.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "hooks" / "validate_workflow.py"


def _valid_workflow():
    """A minimal but realistic config that passes every rule."""
    return {
        "linear": {"team": "ENG", "pickup_state": "Todo"},
        "label": {
            "cadence_active": "cadence-active",
            "cadence_needs_human": "cadence-needs-human",
            "cadence_approve": "cadence-approve",
            "cadence_rework": "cadence-rework",
        },
        "entry": "plan",
        "states": {
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
                "linear_state": "Implementing", "next": "done",
            },
            "done": {"type": "terminal", "linear_state": "Done"},
        },
    }


def run_validator(tmpdir, wf=None, agents=("planner", "implementer"),
                  evidence=False, workflow_text=None, workflow_path=None):
    """Materialise workflow + agent files under tmpdir and invoke the script.

    `wf` is a dict that will be dumped to YAML. `workflow_text` overrides
    that with raw text (for testing unparseable YAML). `workflow_path`
    overrides the destination path entirely (for missing-file tests).
    """
    tmpdir = Path(tmpdir)
    agent_dir = tmpdir / ".claude" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    for a in agents:
        (agent_dir / f"{a}.md").write_text("# agent\n", encoding="utf-8")

    if workflow_path is None:
        workflow_path = tmpdir / "workflow.yaml"
        if workflow_text is not None:
            workflow_path.write_text(workflow_text, encoding="utf-8")
        else:
            workflow_path.write_text(
                yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")

    args = [sys.executable, str(SCRIPT), "--workflow-path", str(workflow_path)]
    if evidence:
        args.append("--evidence")
    return subprocess.run(args, cwd=str(tmpdir),
                          capture_output=True, text=True)


def _rule(evidence, rule_num):
    return next(e for e in evidence if e["rule"] == rule_num)


class ValidateWorkflowTests(unittest.TestCase):

    def test_default_workflow_passes(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            payload = json.loads(r.stdout)
            self.assertTrue(payload["valid"])
            self.assertEqual(payload["entry_state_name"], "plan")
            self.assertEqual(payload["entry_subagent"], "planner")
            self.assertEqual(payload["pickup_state"], "Todo")

    # ---------- evidence shape ----------

    def test_evidence_emits_all_eight_rules(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            payload = json.loads(r.stdout)
            self.assertIn("evidence", payload)
            self.assertEqual(
                sorted(e["rule"] for e in payload["evidence"]),
                [1, 2, 3, 4, 5, 6, 7, 8],
            )
            for e in payload["evidence"]:
                self.assertIn("title", e)
                self.assertIn("lines", e)
                self.assertIsInstance(e["lines"], list)
                self.assertIn(e["result"], ("PASS", "FAIL"))
                self.assertIn("failure", e)

    # ---------- workflow_linear_states ordering ----------

    def test_workflow_linear_states_order(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow())
            payload = json.loads(r.stdout)
            self.assertEqual(
                payload["workflow_linear_states"],
                ["Todo", "Planning", "Plan Review", "Implementing", "Done"],
            )

    # ---------- exit codes ----------

    def test_exit_code_1_on_unparseable_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, workflow_text="foo: [unterminated\n")
            self.assertEqual(r.returncode, 1)
            self.assertIn("invalid YAML", r.stderr)

    def test_exit_code_1_on_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, workflow_path=Path(td) / "nope.yaml")
            self.assertEqual(r.returncode, 1)
            self.assertIn("not found", r.stderr)

    def test_exit_code_1_on_non_mapping_root(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, workflow_text="- just a list\n- of items\n")
            self.assertEqual(r.returncode, 1)
            self.assertIn("did not parse to a mapping", r.stderr)

    def test_exit_code_2_on_rule_failure(self):
        wf = _valid_workflow()
        wf["states"]["implement"]["linear_state"] = "Planning"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf)
            self.assertEqual(r.returncode, 2)

    # ---------- rule 1: uniqueness ----------

    def test_rule1_pass(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 1)["result"],
                             "PASS")

    def test_rule1_fail_duplicate_linear_state(self):
        wf = _valid_workflow()
        wf["states"]["implement"]["linear_state"] = "Planning"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            ev = _rule(json.loads(r.stdout)["evidence"], 1)
            self.assertEqual(ev["result"], "FAIL")
            self.assertIn("Planning", ev["failure"])

    def test_rule1_fail_pickup_collides_with_state(self):
        wf = _valid_workflow()
        wf["linear"]["pickup_state"] = "Planning"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 1)["result"],
                             "FAIL")

    # ---------- rule 2: entry ----------

    def test_rule2_pass(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 2)["result"],
                             "PASS")

    def test_rule2_fail_entry_not_defined(self):
        wf = _valid_workflow()
        wf["entry"] = "ghost"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            ev = _rule(json.loads(r.stdout)["evidence"], 2)
            self.assertEqual(ev["result"], "FAIL")
            self.assertIn("ghost", ev["failure"])

    def test_rule2_fail_entry_not_agent(self):
        wf = _valid_workflow()
        wf["entry"] = "plan_review"  # entry pointing at a gate
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            ev = _rule(json.loads(r.stdout)["evidence"], 2)
            self.assertEqual(ev["result"], "FAIL")
            self.assertIn("must be `agent`", ev["failure"])

    def test_rule2_fail_entry_missing(self):
        wf = _valid_workflow()
        del wf["entry"]
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 2)["result"],
                             "FAIL")

    # ---------- rule 3: targets ----------

    def test_rule3_pass(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 3)["result"],
                             "PASS")

    def test_rule3_fail_dangling_next(self):
        wf = _valid_workflow()
        wf["states"]["plan"]["next"] = "ghost"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            ev = _rule(json.loads(r.stdout)["evidence"], 3)
            self.assertEqual(ev["result"], "FAIL")
            self.assertIn("ghost", ev["failure"])

    def test_rule3_fail_dangling_on_rework(self):
        wf = _valid_workflow()
        wf["states"]["plan_review"]["on_rework"] = "ghost"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 3)["result"],
                             "FAIL")

    # ---------- rule 4: subagent files ----------

    def test_rule4_pass(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 4)["result"],
                             "PASS")

    def test_rule4_fail_missing_agent_file(self):
        # Don't materialise the planner agent file.
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), agents=("implementer",),
                              evidence=True)
            self.assertEqual(r.returncode, 2)
            ev = _rule(json.loads(r.stdout)["evidence"], 4)
            self.assertEqual(ev["result"], "FAIL")
            self.assertIn("planner", ev["failure"])

    def test_rule4_fail_missing_subagent_key(self):
        wf = _valid_workflow()
        del wf["states"]["plan"]["subagent"]
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            ev = _rule(json.loads(r.stdout)["evidence"], 4)
            self.assertEqual(ev["result"], "FAIL")

    # ---------- rule 5: pickup state ----------

    def test_rule5_pass(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 5)["result"],
                             "PASS")

    def test_rule5_fail_empty_pickup(self):
        wf = _valid_workflow()
        wf["linear"]["pickup_state"] = ""
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 5)["result"],
                             "FAIL")

    def test_rule5_fail_missing_pickup(self):
        wf = _valid_workflow()
        del wf["linear"]["pickup_state"]
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 5)["result"],
                             "FAIL")

    # ---------- rule 6: max_in_flight ----------
    # AC-3: removing _rule6_max_in_flight from main() must break these.

    def test_rule6_pass_when_no_caps(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            ev = _rule(json.loads(r.stdout)["evidence"], 6)
            self.assertEqual(ev["result"], "PASS")
            self.assertEqual(ev["title"], "max_in_flight type and scope")

    def test_rule6_pass_with_valid_caps_on_agent_and_gate(self):
        wf = _valid_workflow()
        wf["states"]["plan"]["max_in_flight"] = 3
        wf["states"]["plan_review"]["max_in_flight"] = 5
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 6)["result"],
                             "PASS")

    def test_rule6_fail_on_terminal(self):
        wf = _valid_workflow()
        wf["states"]["done"]["max_in_flight"] = 1
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            ev = _rule(json.loads(r.stdout)["evidence"], 6)
            self.assertEqual(ev["result"], "FAIL")
            self.assertIn("done", ev["failure"])

    def test_rule6_fail_zero(self):
        wf = _valid_workflow()
        wf["states"]["plan"]["max_in_flight"] = 0
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 6)["result"],
                             "FAIL")

    def test_rule6_fail_bool_rejected(self):
        # The script must reject booleans even though bool is a subclass of int.
        wf = _valid_workflow()
        wf["states"]["plan"]["max_in_flight"] = True
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 6)["result"],
                             "FAIL")

    def test_rule6_fail_string(self):
        wf = _valid_workflow()
        wf["states"]["plan"]["max_in_flight"] = "3"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 6)["result"],
                             "FAIL")

    # ---------- rule 7: adversarial_context ----------

    def test_rule7_pass_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 7)["result"],
                             "PASS")

    def test_rule7_pass_with_bool_on_agent(self):
        wf = _valid_workflow()
        wf["states"]["implement"]["adversarial_context"] = True
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 7)["result"],
                             "PASS")

    def test_rule7_fail_on_gate(self):
        wf = _valid_workflow()
        wf["states"]["plan_review"]["adversarial_context"] = True
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 7)["result"],
                             "FAIL")

    def test_rule7_fail_non_bool(self):
        wf = _valid_workflow()
        wf["states"]["implement"]["adversarial_context"] = "yes"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 7)["result"],
                             "FAIL")

    # ---------- linear_to_workflow reverse map (P2 determinism) ----------
    # AC-1/2/3: tick.md step 6 and status.md step 2 both consume this map.

    def test_linear_to_workflow_default_workflow_shape(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            mapping = json.loads(r.stdout)["linear_to_workflow"]
            # AC-1: agent state's linear_state.
            self.assertEqual(
                mapping["Implementing"],
                {"kind": "state", "workflow_state": "implement",
                 "linear_state_type": "agent"},
            )
            # AC-2: pickup column.
            self.assertEqual(
                mapping["Todo"],
                {"kind": "pickup", "workflow_state": None,
                 "linear_state_type": None},
            )
            # AC-3: gate's waiting column.
            self.assertEqual(
                mapping["Plan Review"],
                {"kind": "gate_waiting", "workflow_state": "plan_review",
                 "linear_state_type": "gate"},
            )
            # Terminal column.
            self.assertEqual(
                mapping["Done"],
                {"kind": "state", "workflow_state": "done",
                 "linear_state_type": "terminal"},
            )

    def test_linear_to_workflow_custom_pickup_name(self):
        wf = _valid_workflow()
        wf["linear"]["pickup_state"] = "Backlog"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf)
            mapping = json.loads(r.stdout)["linear_to_workflow"]
            self.assertEqual(
                mapping["Backlog"],
                {"kind": "pickup", "workflow_state": None,
                 "linear_state_type": None},
            )
            self.assertNotIn("Todo", mapping)

    def test_linear_to_workflow_gate_only_workflow(self):
        # A workflow whose only non-terminal/non-pickup state is a gate's
        # waiting column. Confirms the gate_waiting branch fires even when
        # no agent state contributes to the map.
        wf = {
            "linear": {"team": "ENG", "pickup_state": "Todo"},
            "label": {
                "cadence_active": "cadence-active",
                "cadence_needs_human": "cadence-needs-human",
                "cadence_approve": "cadence-approve",
                "cadence_rework": "cadence-rework",
            },
            "entry": "plan",
            "states": {
                "plan": {
                    "type": "agent", "subagent": "planner",
                    "linear_state": "Planning", "next": "review",
                },
                "review": {
                    "type": "gate", "linear_state": "Review",
                    "on_approve": "done", "on_rework": "plan",
                },
                "done": {"type": "terminal", "linear_state": "Done"},
            },
        }
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, agents=("planner",))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            mapping = json.loads(r.stdout)["linear_to_workflow"]
            self.assertEqual(mapping["Review"]["kind"], "gate_waiting")
            self.assertEqual(mapping["Review"]["workflow_state"], "review")
            self.assertEqual(mapping["Review"]["linear_state_type"], "gate")

    def test_linear_to_workflow_keys_are_workflow_linear_states(self):
        # The map's keys should be exactly the workflow_linear_states set.
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow())
            payload = json.loads(r.stdout)
            self.assertEqual(
                sorted(payload["linear_to_workflow"].keys()),
                sorted(payload["workflow_linear_states"]),
            )

    # ---------- raw config pass-through (P2 determinism) ----------
    # The validator emits the raw `linear`, `label`, `limits` blocks so
    # dispatch prose reads them from the script's JSON instead of doing
    # its own Read of workflow.yaml. Without this, the LLM caches the
    # earlier Read across fires and edits to the YAML go unnoticed.

    def test_raw_linear_block_passthrough(self):
        wf = _valid_workflow()
        wf["linear"]["project_slug"] = "cadence"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf)
            payload = json.loads(r.stdout)
            self.assertEqual(
                payload["linear"],
                {"team": "ENG", "pickup_state": "Todo", "project_slug": "cadence"},
            )

    def test_raw_label_block_passthrough(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow())
            payload = json.loads(r.stdout)
            self.assertEqual(
                payload["label"],
                {
                    "cadence_active": "cadence-active",
                    "cadence_needs_human": "cadence-needs-human",
                    "cadence_approve": "cadence-approve",
                    "cadence_rework": "cadence-rework",
                },
            )

    def test_raw_limits_block_passthrough(self):
        wf = _valid_workflow()
        wf["limits"] = {"max_attempts_per_issue": 3, "stale_after_minutes": 45}
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf)
            payload = json.loads(r.stdout)
            self.assertEqual(
                payload["limits"],
                {"max_attempts_per_issue": 3, "stale_after_minutes": 45},
            )

    def test_raw_blocks_absent_default_to_empty_dict(self):
        # A config that omits `label` and `limits` entirely should still
        # surface them as `{}` (so prose can `.get(...)` without crashing).
        wf = _valid_workflow()
        del wf["label"]
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["label"], {})
            self.assertEqual(payload["limits"], {})

    def test_raw_blocks_nondict_coerced_to_empty(self):
        # YAML that puts a string where a mapping should be must not
        # blow up downstream consumers.
        wf = _valid_workflow()
        wf["label"] = "not a mapping"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["label"], {})

    # ---------- rule 8: legacy gate keys ----------

    def test_rule8_pass(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 8)["result"],
                             "PASS")

    def test_rule8_fail_approved_linear_state(self):
        wf = _valid_workflow()
        wf["states"]["plan_review"]["approved_linear_state"] = "Approved"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            ev = _rule(json.loads(r.stdout)["evidence"], 8)
            self.assertEqual(ev["result"], "FAIL")
            self.assertIn("approved_linear_state", ev["failure"])

    def test_rule8_fail_rework_linear_state(self):
        wf = _valid_workflow()
        wf["states"]["plan_review"]["rework_linear_state"] = "Rework"
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, wf, evidence=True)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(_rule(json.loads(r.stdout)["evidence"], 8)["result"],
                             "FAIL")


class ScratchDirTests(unittest.TestCase):
    """The validator creates `.cadence/` + a self-ignoring `.gitignore` so the
    dispatch prose's scratch JSON (validator output, comment lists, etc.) never
    shows up in the consumer's `git status` — even on the dry-run path, which
    fires no Linear write and so never triggers the audit hook."""

    def test_valid_run_creates_self_ignoring_cadence_dir(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            gi = Path(td) / ".cadence" / ".gitignore"
            self.assertTrue(gi.is_file())
            self.assertEqual(gi.read_text(encoding="utf-8"), "*\n")

    def test_evidence_run_creates_cadence_dir(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_validator(td, _valid_workflow(), evidence=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertTrue((Path(td) / ".cadence" / ".gitignore").is_file())

    def test_existing_gitignore_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            cadence = Path(td) / ".cadence"
            cadence.mkdir()
            (cadence / ".gitignore").write_text("custom\n", encoding="utf-8")
            r = run_validator(td, _valid_workflow())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(
                (cadence / ".gitignore").read_text(encoding="utf-8"),
                "custom\n")


class ValidateEntrypointTests(unittest.TestCase):
    """Direct-import smoke tests for the reusable `validate()` entrypoint the
    three deterministic consumers call via `load_config(--workflow-path)`."""

    def _validate(self, td, wf):
        """Run `validate(path)` in a subprocess with cwd=td (Rule 4 resolves
        agent files relative to cwd) and return the parsed (valid, fail_rules).
        """
        agent_dir = td / ".claude" / "agents"
        agent_dir.mkdir(parents=True, exist_ok=True)
        for a in ("planner", "implementer"):
            (agent_dir / f"{a}.md").write_text("# agent\n", encoding="utf-8")
        wf_path = td / "workflow.yaml"
        wf_path.write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
        code = (
            "import json, sys; sys.path.insert(0, %r); import validate_workflow;"
            "r, ev = validate_workflow.validate(%r);"
            "print(json.dumps({'valid': r['valid'],"
            " 'fails': [e['rule'] for e in ev if e['result'] == 'FAIL']}))"
            % (str(SCRIPT.parent), str(wf_path))
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(td),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return json.loads(r.stdout)

    def test_validate_returns_valid_for_good_config(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._validate(Path(td), _valid_workflow())
            self.assertTrue(out["valid"])
            self.assertEqual(out["fails"], [])

    def test_validate_returns_failing_rule_for_bad_config(self):
        wf = _valid_workflow()
        wf["states"]["plan"]["next"] = "nonexistent"
        with tempfile.TemporaryDirectory() as td:
            out = self._validate(Path(td), wf)
            self.assertFalse(out["valid"])
            self.assertIn(3, out["fails"])


if __name__ == "__main__":
    unittest.main()
