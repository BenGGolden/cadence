"""Tests for templates/cadence/hooks/compose_lifecycle_context.py.

Covers:
  - Default (non-adversarial, non-rework) render.
  - Adversarial-context render (with / without PR URL, --default-branch).
  - Rework section: N>0 comments, zero-comments fallback, multi-line bodies.
  - Branch derivation (verbatim vs derived).
  - Priority rendering (numeric vs null).
  - Labels rendering ((none) vs comma-separated).
  - globalPrompt append (present vs missing).
  - --dry-run byte-identical to the stored fixture.
  - Required-arg validation and JSON shape errors.

Goldens for the rendered output live under tests/fixtures/lifecycle_context/.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "cadence" / "hooks" / "compose_lifecycle_context.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "lifecycle_context"


def _default_states():
    return {
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
            "linear_state": "Reviewing", "next": "human_review",
            "adversarial_context": True,
        },
        "human_review": {
            "type": "gate", "linear_state": "In Review",
            "on_approve": "done", "on_rework": "implement",
        },
        "done": {"type": "terminal", "linear_state": "Done"},
    }


def _default_config(states=None, team="ENG", entry="plan"):
    """Mimic the validator's JSON output shape for the compose script."""
    states = states if states is not None else _default_states()
    return {
        "valid": True,
        "entry_state_name": entry,
        "entry_subagent": (states.get(entry) or {}).get("subagent"),
        "linear": {"team": team, "pickup_state": "Todo"},
        "label": {
            "cadence_active": "cadence-active",
            "cadence_needs_human": "cadence-needs-human",
            "cadence_approve": "cadence-approve",
            "cadence_rework": "cadence-rework",
        },
        "limits": {"max_attempts_per_issue": 3},
        "states": states,
    }


def _default_issue():
    return {
        "identifier": "ENG-42",
        "title": "Wire up new dashboard widget",
        "url": "https://linear.app/eng/issue/ENG-42",
        "branchName": "eng/eng-42-wire-up-dashboard-widget",
        "priority": 2,
        "labels": [{"name": "frontend"}, {"name": "P1"}],
        "description": ("Add the widget. Plot foo vs bar.\n\n"
                        "## Acceptance Criteria\n"
                        "- [ ] **AC-1** Widget renders\n"
                        "- [ ] **AC-2** Tests cover empty state"),
    }


def _default_parent():
    return {
        # This connector returns the identifier under `id` (no separate
        # `identifier` field); the render helper reads `identifier or id`.
        "id": "ENG-1",
        "title": "Dashboard revamp epic",
        "description": ("Shared spec for the dashboard revamp.\n\n"
                        "## Shared Acceptance Criteria\n"
                        "- [ ] All widgets use the shared theme tokens.\n"
                        "- [ ] Empty states follow the common pattern."),
    }


def _parse_output(rework_context=None, pr_url=None):
    return {
        "latest_tracking_comment": {
            "kind": "state", "state": "implement", "attempt": 1,
            "status": None, "raw_json": {},
        },
        "attempt_count": 1,
        "rework_count": 0,
        "rework_context": rework_context or [],
        "latest_implementer_summary": {
            "pr_url": pr_url,
            "branch": "eng/eng-42-wire-up-dashboard-widget" if pr_url else None,
        },
        "parse_errors": [],
    }


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def run_compose(tmpdir, *, config, issue=None, target_state=None, attempt=None,
                parse_output=None, rework=False, dry_run=False,
                global_prompt_path=None, default_branch=None, parent=None,
                parent_warn_chars=None, parent_max_chars=None, extra_args=()):
    tmpdir = Path(tmpdir)
    cfg_path = tmpdir / "config.json"
    _write(cfg_path, config)
    args = [sys.executable, str(SCRIPT), "--workflow-config", str(cfg_path)]
    if dry_run:
        args.append("--dry-run")
    else:
        issue_path = tmpdir / "issue.json"
        _write(issue_path, issue or {})
        parse_path = tmpdir / "parse.json"
        _write(parse_path, parse_output or _parse_output())
        args += [
            "--issue", str(issue_path),
            "--target-state", target_state,
            "--attempt", str(attempt),
            "--parse-comments-output", str(parse_path),
        ]
    if parent is not None:
        parent_path = tmpdir / "parent.json"
        _write(parent_path, parent)
        args += ["--parent", str(parent_path)]
    if parent_warn_chars is not None:
        args += ["--parent-warn-chars", str(parent_warn_chars)]
    if parent_max_chars is not None:
        args += ["--parent-max-chars", str(parent_max_chars)]
    if rework:
        args.append("--rework")
    if global_prompt_path is not None:
        args += ["--global-prompt-path", str(global_prompt_path)]
    if default_branch is not None:
        args += ["--default-branch", default_branch]
    args.extend(extra_args)
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8")


class ComposeLifecycleContextTests(unittest.TestCase):

    # ---------- default render ----------

    def test_default_render_matches_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            expected = (FIXTURES / "default.md").read_text(encoding="utf-8")
            self.assertEqual(r.stdout, expected)

    def test_default_render_next_is_gate_includes_gate_line(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="plan", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("- Gate downstream: human will see this in Linear "
                          "column \"Plan Review\" and decide approve/rework.",
                          r.stdout)
            self.assertNotIn("Terminal state", r.stdout)

    def test_default_render_next_is_terminal_includes_terminal_line(self):
        states = _default_states()
        # Make implement's next a terminal directly.
        states["implement"]["next"] = "done"
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(states=states),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("- Terminal state: the bootstrap will close the "
                          "workflow at \"Done\".", r.stdout)
            self.assertNotIn("Gate downstream", r.stdout)

    def test_default_render_next_is_agent_no_extra_line(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertNotIn("Gate downstream", r.stdout)
            self.assertNotIn("Terminal state", r.stdout)

    # ---------- adversarial render ----------

    def test_adversarial_render_matches_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="agent_review", attempt=1,
                            parse_output=_parse_output(
                                pr_url="https://github.com/foo/bar/pull/9"))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            expected = (FIXTURES / "adversarial.md").read_text(encoding="utf-8")
            self.assertEqual(r.stdout, expected)

    def test_adversarial_no_pr_url_omits_pr_line(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="agent_review", attempt=1,
                            parse_output=_parse_output(pr_url=None))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertNotIn("**PR:**", r.stdout)
            self.assertIn("**Branch (under review):**", r.stdout)
            self.assertIn("**Base branch:** main", r.stdout)

    def test_adversarial_default_branch_flag_honoured(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="agent_review", attempt=1,
                            parse_output=_parse_output(),
                            default_branch="develop")
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("**Base branch:** develop", r.stdout)

    def test_adversarial_with_rework_keeps_rework_section(self):
        rework = [
            {"body": "still broken", "author": "Alice",
             "createdAt": "2026-05-27T10:00:00Z"},
        ]
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="agent_review", attempt=2,
                            parse_output=_parse_output(rework_context=rework),
                            rework=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("### Rework Context", r.stdout)
            self.assertIn("> still broken", r.stdout)
            # Adversarial bullets still there.
            self.assertIn("**Branch (under review):**", r.stdout)

    # ---------- rework section ----------

    def test_rework_two_comments_matches_fixture(self):
        rework = [
            {"body": "The empty state still crashes when there's no data.\n"
                     "Please add the fallback.",
             "author": "Alice",
             "createdAt": "2026-05-27T10:00:00Z"},
            {"body": "Also rename the widget id to dashboard-foo-widget",
             "author": "Bob",
             "createdAt": "2026-05-27T11:00:00Z"},
        ]
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=2,
                            parse_output=_parse_output(rework_context=rework),
                            rework=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            expected = (FIXTURES / "rework.md").read_text(encoding="utf-8")
            self.assertEqual(r.stdout, expected)

    def test_rework_marked_but_no_comments_emits_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=2,
                            parse_output=_parse_output(rework_context=[]),
                            rework=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("### Rework Context", r.stdout)
            self.assertIn("No human comments were left when this issue was "
                          "sent back", r.stdout)

    def test_no_rework_flag_omits_section_even_with_context(self):
        # Even if parse output has rework_context (e.g. step 6 ran with
        # --gate-name), the section is gated on the explicit --rework flag.
        rework = [{"body": "x", "author": "A", "createdAt": "2026-05-27"}]
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output(rework_context=rework),
                            rework=False)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertNotIn("### Rework Context", r.stdout)

    # ---------- parent context ----------

    def test_parent_present_renders_section_matches_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output(),
                            parent=_default_parent())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            expected = (FIXTURES / "parent.md").read_text(encoding="utf-8")
            self.assertEqual(r.stdout, expected)

    def test_parent_absent_omits_section(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertNotIn("### Parent Context", r.stdout)

    def test_parent_empty_description_omits_section(self):
        for desc in ("", "   \n\t  "):
            parent = _default_parent()
            parent["description"] = desc
            with tempfile.TemporaryDirectory() as td:
                r = run_compose(td, config=_default_config(),
                                issue=_default_issue(),
                                target_state="implement", attempt=1,
                                parse_output=_parse_output(),
                                parent=parent)
                self.assertEqual(r.returncode, 0, msg=r.stderr)
                self.assertNotIn("### Parent Context", r.stdout)

    def test_parent_section_placed_between_description_and_transitions(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output(),
                            parent=_default_parent())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            desc_i = r.stdout.index("### Description")
            parent_i = r.stdout.index("### Parent Context")
            trans_i = r.stdout.index("### Transitions")
            self.assertLess(desc_i, parent_i)
            self.assertLess(parent_i, trans_i)

    def test_parent_rendered_in_adversarial_variant(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="agent_review", attempt=1,
                            parse_output=_parse_output(),
                            parent=_default_parent())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("### Parent Context", r.stdout)
            self.assertIn("**Branch (under review):**", r.stdout)

    def test_parent_coexists_with_rework(self):
        rework = [{"body": "fix it", "author": "Alice",
                   "createdAt": "2026-05-27T10:00:00Z"}]
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=2,
                            parse_output=_parse_output(rework_context=rework),
                            rework=True, parent=_default_parent())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("### Parent Context", r.stdout)
            self.assertIn("### Rework Context", r.stdout)

    def test_parent_over_warn_under_ceiling_warns_and_inherits_full(self):
        parent = _default_parent()
        parent["description"] = "x" * 5_000
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output(),
                            parent=parent,
                            parent_warn_chars=100, parent_max_chars=0)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("CADENCE_WARNING", r.stderr)
            self.assertIn("x" * 5_000, r.stdout)          # full text, not cut
            self.assertNotIn("truncated", r.stdout)

    def test_parent_over_ceiling_fails_fire(self):
        parent = _default_parent()
        parent["description"] = "y" * 5_000
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output(),
                            parent=parent, parent_max_chars=100)
            self.assertEqual(r.returncode, 2, msg=r.stderr)
            self.assertIn("hard ceiling", r.stderr)
            self.assertNotIn("### Parent Context", r.stdout)

    def test_parent_under_warn_silent(self):
        parent = _default_parent()
        parent["description"] = "z" * 50
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output(),
                            parent=parent)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertNotIn("CADENCE_WARNING", r.stderr)
            self.assertIn("z" * 50, r.stdout)

    def test_parent_thresholds_zero_disabled(self):
        parent = _default_parent()
        parent["description"] = "w" * 50_000
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output(),
                            parent=parent,
                            parent_warn_chars=0, parent_max_chars=0)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertNotIn("CADENCE_WARNING", r.stderr)
            self.assertIn("w" * 50_000, r.stdout)

    # ---------- branch derivation ----------

    def test_branch_uses_issue_branchname_when_present(self):
        issue = _default_issue()
        issue["branchName"] = "custom/branch-name"
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(), issue=issue,
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("**Branch (Linear suggested):** custom/branch-name",
                          r.stdout)

    def test_branch_derived_from_team_id_title_when_branchname_absent(self):
        issue = _default_issue()
        del issue["branchName"]
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(team="ENG"),
                            issue=issue,
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn(
                "**Branch (Linear suggested):** "
                "eng/eng-42-wire-up-new-dashboard-widget",
                r.stdout,
            )

    def test_branch_derived_title_slug_truncates_to_50_chars(self):
        issue = _default_issue()
        del issue["branchName"]
        # 70-char title produces a >50-char slug; the derivation must trim it.
        issue["title"] = "a" * 70
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(team="X"),
                            issue=issue,
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # The slug portion (after the dash separating identifier from
            # title) should be 50 chars.
            for line in r.stdout.splitlines():
                if line.startswith("- **Branch"):
                    branch = line.split(":** ", 1)[1]
                    # branch = "x/eng-42-aaaaa...". Slug starts after "eng-42-".
                    slug = branch.split("/", 1)[1].split("-", 2)[2]
                    self.assertEqual(len(slug), 50)
                    break
            else:
                self.fail("Branch line not found in output")

    # ---------- priority rendering ----------

    def test_priority_numeric_rendered_with_label(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),  # priority 2
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("**Priority:** 2 (High)", r.stdout)

    def test_priority_null_renders_none(self):
        issue = _default_issue()
        issue["priority"] = None
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=issue,
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("**Priority:** (none)", r.stdout)

    # ---------- labels rendering ----------

    def test_labels_empty_renders_none(self):
        issue = _default_issue()
        issue["labels"] = []
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=issue,
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("**Labels:** (none)", r.stdout)

    def test_labels_comma_separated(self):
        issue = _default_issue()
        issue["labels"] = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(),
                            issue=issue,
                            target_state="implement", attempt=1,
                            parse_output=_parse_output())
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("**Labels:** a, b, c", r.stdout)

    # ---------- globalPrompt append ----------

    def test_global_prompt_present_appears_after_two_blank_lines(self):
        with tempfile.TemporaryDirectory() as td:
            gp = Path(td) / "global.md"
            gp.write_text("hello from global\n", encoding="utf-8")
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output(),
                            global_prompt_path=gp)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # Block ends with the END marker, then "\n\n\n", then
            # global prompt content.
            self.assertIn(
                "<!-- END CADENCE LIFECYCLE -->\n\n\nhello from global\n",
                r.stdout,
            )

    def test_global_prompt_missing_no_trailing_blank_lines(self):
        with tempfile.TemporaryDirectory() as td:
            gp = Path(td) / "global.md"  # never created
            r = run_compose(td, config=_default_config(),
                            issue=_default_issue(),
                            target_state="implement", attempt=1,
                            parse_output=_parse_output(),
                            global_prompt_path=gp)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            # Output ends with END marker + single newline. No phantom blanks.
            self.assertTrue(r.stdout.endswith(
                "<!-- END CADENCE LIFECYCLE -->\n"))
            self.assertFalse(r.stdout.endswith("\n\n\n"))

    # ---------- dry-run ----------

    def test_dry_run_matches_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(), dry_run=True)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            expected = (FIXTURES / "dry_run.md").read_text(encoding="utf-8")
            self.assertEqual(r.stdout, expected)

    def test_dry_run_ignores_live_args(self):
        # Even if --issue / --target-state / --attempt are passed, --dry-run
        # uses the entry-state placeholders.
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=_default_config(), dry_run=True,
                            extra_args=(
                                "--issue", "/nonexistent.json",
                                "--target-state", "agent_review",
                                "--attempt", "99",
                            ))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("**Issue:** EXAMPLE-1", r.stdout)
            self.assertIn("**State:** plan", r.stdout)
            self.assertIn("**Attempt:** 1", r.stdout)

    # ---------- error paths ----------

    def test_missing_required_arg_exits_1(self):
        # --issue is required without --dry-run.
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.json"
            _write(cfg_path, _default_config())
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-config", str(cfg_path),
                 "--target-state", "implement", "--attempt", "1",
                 "--parse-comments-output", "/tmp/whatever"],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("--issue", r.stderr)

    def test_malformed_issue_json_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.json"
            _write(cfg_path, _default_config())
            issue_path = Path(td) / "issue.json"
            issue_path.write_text("{not json", encoding="utf-8")
            parse_path = Path(td) / "parse.json"
            _write(parse_path, _parse_output())
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-config", str(cfg_path),
                 "--issue", str(issue_path),
                 "--target-state", "implement", "--attempt", "1",
                 "--parse-comments-output", str(parse_path)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("--issue", r.stderr)

    def test_dry_run_without_entry_state_exits_1(self):
        cfg = _default_config()
        cfg["entry_state_name"] = None
        with tempfile.TemporaryDirectory() as td:
            r = run_compose(td, config=cfg, dry_run=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("entry state", r.stderr)


def _workflow_yaml_dict():
    """A `.claude/workflow.yaml` whose validated config matches the dict
    `_default_config()` builds, so the two run modes compare."""
    return {
        "linear": {"team": "ENG", "pickup_state": "Todo"},
        "label": {
            "cadence_active": "cadence-active",
            "cadence_needs_human": "cadence-needs-human",
            "cadence_approve": "cadence-approve",
            "cadence_rework": "cadence-rework",
        },
        "limits": {"max_attempts_per_issue": 3},
        "entry": "plan",
        "states": _default_states(),
    }


def _materialise_workflow(td, wf):
    """Write `.claude/workflow.yaml` + the three agent files under td."""
    claude = td / ".claude"
    agents = claude / "agents" / "cadence"
    agents.mkdir(parents=True, exist_ok=True)
    for a in ("planner", "implementer", "reviewer"):
        (agents / f"{a}.md").write_text("# agent\n", encoding="utf-8")
    wf_path = claude / "workflow.yaml"
    wf_path.write_text(yaml.safe_dump(wf, sort_keys=False), encoding="utf-8")
    return wf_path


class WorkflowPathModeTests(unittest.TestCase):
    """--workflow-path mode: validate .claude/workflow.yaml internally."""

    def test_live_render_parity(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg_out = run_compose(
                td, config=_default_config(), issue=_default_issue(),
                target_state="implement", attempt=1,
                parse_output=_parse_output()).stdout

            wf_path = _materialise_workflow(td, _workflow_yaml_dict())
            issue_path = td / "issue.json"
            _write(issue_path, _default_issue())
            parse_path = td / "parse.json"
            _write(parse_path, _parse_output())
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-path", str(wf_path),
                 "--issue", str(issue_path),
                 "--target-state", "implement", "--attempt", "1",
                 "--parse-comments-output", str(parse_path)],
                cwd=str(td), capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(r.stdout, cfg_out)

    def test_dry_run_parity(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cfg_out = run_compose(td, config=_default_config(),
                                  dry_run=True).stdout

            wf_path = _materialise_workflow(td, _workflow_yaml_dict())
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-path", str(wf_path), "--dry-run"],
                cwd=str(td), capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertEqual(r.stdout, cfg_out)

    def test_invalid_workflow_bails_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            wf = _workflow_yaml_dict()
            wf["states"]["implement"]["next"] = "nonexistent"
            wf_path = _materialise_workflow(td, wf)
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--workflow-path", str(wf_path), "--dry-run"],
                cwd=str(td), capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 2)
            self.assertIn("Rule 3 (Targets) FAILED", r.stderr)
            self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
