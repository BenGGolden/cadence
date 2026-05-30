#!/usr/bin/env python3
"""Decide what one Cadence /cadence:tick fire should do to its locked issue.

Caller:
  - commands/tick.md "Route" step (replaces the model-executed branching that
    used to live in steps 8–11).

This is the *decision core* of a fire, extracted out of dispatch prose into a
pure, testable orchestrator (GUIDEPOSTS #7 — determinism). It answers one
question — "given where this issue sits and its history, what should this fire
do to it?" — as a pure function of `(config, current Linear column, present
labels, comment history)`. It performs **no** MCP, network, or shell-out
calls; it only emits a plan. The bootstrap remains the sole Linear writer and
executes every action.

It wires three pure helpers:
  - parse_comments.parse_comment_list  — comment history → structured facts
                                         (run exactly once per fire here).
  - classify_drift.classify_drift      — the old step-9 drift branch.
  - classify_gate.classify_gate        — the old step-10 gate verdict routing.
And imports emit_tracking_comment's formatters so the plan carries finished
tracking-comment bodies (reconcile / gate rework / gate escalation), never
re-templated inline.

CLI:
  python route_fire.py
    [--workflow-config <validatorJson> | --workflow-path <workflow.yaml>]
    --linear-state "<current Linear column>"
    --comments <commentsFile>
    --labels <labelsFile|csv>

  --linear-state  the issue's column AFTER the step-7 pickup move.
  --comments      the issue's full comment list as a JSON array.
  --labels        the present label names: a path to a JSON file (array of
                  names or label dicts) OR a comma-separated string.

Stdout: the plan as JSON (see the shapes in _invoke_plan / _exit_plan).
Exit codes:
  0  plan emitted on stdout
  1  bad / missing required input (config unreadable, etc.)
"""

import argparse
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import classify_drift
import classify_gate
import emit_tracking_comment
import parse_comments
import validate_workflow

# The plan can carry Linear column names / comment bodies with non-ASCII
# characters; force UTF-8 so stdout is stable regardless of the parent
# locale (Windows defaults to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  newline="")


# ----- action constructors -------------------------------------------------

def _post_comment(body):
    return {"type": "post_comment", "body": body}


def _remove_label(name):
    return {"type": "remove_label", "label": name}


def _add_label(name):
    return {"type": "add_label", "label": name}


def _move_state(linear_state):
    return {"type": "move_state", "linear_state": linear_state}


# ----- comment-body formatters (imported, never re-templated) ---------------

def _gate_body(state, status, rework_to=None):
    ns = SimpleNamespace(state=state, status=status, rework_to=rework_to)
    return emit_tracking_comment.build_gate(ns)


def _reconcile_body(observed, expected, reason):
    ns = SimpleNamespace(observed_linear_state=observed,
                         expected_state=expected, reason=reason)
    return emit_tracking_comment.build_reconcile(ns)


# ----- plan constructors ---------------------------------------------------

def _invoke_plan(matched, target, attempt, pre_actions, subagent, rework,
                 parse_output):
    return {
        "matched_state": matched,
        "target_state": target,
        "attempt": attempt,
        "rework": rework,
        "pre_actions": pre_actions,
        "invoke_subagent": True,
        "subagent": subagent,
        # The full parse_comments result. The old step-9 prose wrote this to
        # a file for step 13's compose_lifecycle_context (rework_context +
        # latest_implementer_summary.pr_url). The router parsed exactly once;
        # it hands the result on so step 13 needs no second parse.
        "parse_comments_output": parse_output,
        "exit_plan": None,
        "exit_summary": None,
    }


def _exit_plan(matched, target, exit_actions, summary):
    return {
        "matched_state": matched,
        "target_state": target,
        "attempt": None,
        "rework": False,
        "pre_actions": [],
        "invoke_subagent": False,
        "subagent": None,
        "parse_comments_output": None,
        "exit_plan": exit_actions,
        "exit_summary": summary,
    }


# ----- input loading -------------------------------------------------------

def _load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        print(f"Cadence: could not read {label} from {path}: {e}",
              file=sys.stderr)
        sys.exit(1)


def _load_labels(value):
    """Return the set of present label names.

    `value` is either a path to a JSON file (an array of names, label dicts,
    or the GraphQL `{"nodes": [...]}` connection shape) or a comma-separated
    string of names."""
    names = []
    if value and Path(value).is_file():
        raw = _load_json(value, "--labels")
        if isinstance(raw, dict):
            raw = raw.get("nodes") or raw.get("labels") or []
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict):
                    name = entry.get("name") or entry.get("title")
                    if isinstance(name, str) and name:
                        names.append(name)
                elif isinstance(entry, str) and entry:
                    names.append(entry)
    elif value:
        names = [part.strip() for part in value.split(",") if part.strip()]
    return set(names)


# ----- the router ----------------------------------------------------------

def route(config, linear_state, comments, present_labels):
    """Pure routing decision. Returns the plan dict."""
    states = config.get("states") or {}
    labels = config.get("label") or {}
    limits = config.get("limits") or {}
    ltw = config.get("linear_to_workflow") or {}

    active_label = labels.get("cadence_active")
    approve_label = labels.get("cadence_approve")
    rework_label = labels.get("cadence_rework")
    needs_human_label = labels.get("cadence_needs_human")

    def label_name(key):
        return labels.get(key)

    # --- Step 8: matched workflow state ------------------------------------
    entry = ltw.get(linear_state)
    matched_state = entry.get("workflow_state") if isinstance(entry, dict) else None
    if entry is None or matched_state is None:
        body = (f"**[Cadence]** Issue moved to unmapped Linear state "
                f"`{linear_state}` between pickup and dispatch; releasing "
                f"lock without action.")
        return _exit_plan(
            None, None,
            [_post_comment(body), _remove_label(active_label)],
            f"Issue in unmapped Linear state `{linear_state}`; "
            f"released lock without action.",
        )

    matched_body = states.get(matched_state) or {}
    is_gate = matched_body.get("type") == "gate"

    # --- pre-resolve the verdict + target for the single parse run ---------
    approve_present = approve_label is not None and approve_label in present_labels
    rework_present = rework_label is not None and rework_label in present_labels
    gate_name = matched_state if is_gate else None

    if is_gate:
        if rework_present:
            prelim_target = matched_body.get("on_rework")
        elif approve_present:
            prelim_target = matched_body.get("on_approve")
        else:
            prelim_target = matched_state  # waiting — attempt_count unused
    else:
        prelim_target = matched_state

    # --- Step 9 + 11 data: parse the comment history exactly once ----------
    parsed = parse_comments.parse_comment_list(
        comments, prelim_target, gate_name=gate_name)
    latest_state = (parsed.get("latest_tracking_comment") or {}).get("state")
    attempt_count = parsed.get("attempt_count", 0)
    rework_count = parsed.get("rework_count", 0)

    actions = []
    is_rework = False

    # --- Step 9: drift check ----------------------------------------------
    drift = classify_drift.classify_drift(
        latest_state, matched_state, linear_state, states)
    if drift["drift"]:
        ra = drift["reconcile_args"]
        actions.append(_post_comment(_reconcile_body(
            ra["observed_linear_state"], ra["expected_state"], ra["reason"])))

    # --- Step 10: gate handling -------------------------------------------
    if is_gate:
        gp = classify_gate.classify_gate(
            approve_present, rework_present, matched_body, rework_count)
        verdict = gp["verdict"]

        if verdict == "waiting":
            actions.append(_remove_label(active_label))
            return _exit_plan(
                matched_state, None, actions,
                f"Awaiting human verdict at gate **{matched_state}**; "
                f"released lock.",
            )

        for key in gp["remove_labels"]:
            actions.append(_remove_label(label_name(key)))

        target_state = gp["target_state"]

        if verdict == "rework":
            if gp["escalate"]:
                actions.append(_post_comment(
                    _gate_body(matched_state, "escalated")))
                actions.append(_add_label(needs_human_label))
                actions.append(_remove_label(active_label))
                return _exit_plan(
                    matched_state, target_state, actions,
                    f"Rework limit reached at gate **{matched_state}**; "
                    f"escalated to human.",
                )
            actions.append(_post_comment(
                _gate_body(matched_state, "rework", rework_to=target_state)))
            target_body = states.get(target_state) or {}
            actions.append(_move_state(target_body.get("linear_state")))
            is_rework = True
        else:  # approve
            target_body = states.get(target_state) or {}
            actions.append(_move_state(target_body.get("linear_state")))
            if target_body.get("type") == "terminal":
                actions.append(_remove_label(active_label))
                return _exit_plan(
                    matched_state, target_state, actions,
                    f"Approved at gate **{matched_state}** → terminal "
                    f"**{target_state}**; released lock.",
                )
    else:
        target_state = matched_state

    # --- Step 11: attempt cap (for the RESOLVED target) -------------------
    max_attempts = limits.get("max_attempts_per_issue")
    cap_defined = isinstance(max_attempts, int) and not isinstance(max_attempts, bool)
    if cap_defined and attempt_count >= max_attempts:
        body = (f"**[Cadence]** Max attempts (`{max_attempts}`) reached at "
                f"state **{target_state}**. Needs human intervention.")
        actions.append(_post_comment(body))
        actions.append(_add_label(needs_human_label))
        actions.append(_remove_label(active_label))
        return _exit_plan(
            matched_state, target_state, actions,
            f"Max attempts reached at **{target_state}**; escalated to human.",
        )

    attempt = attempt_count + 1
    subagent = (states.get(target_state) or {}).get("subagent")
    return _invoke_plan(matched_state, target_state, attempt, actions,
                        subagent, is_rework, parsed)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workflow-config", default=None,
                    help="Path to a pre-built validator JSON dict (dry-run/tests).")
    ap.add_argument("--workflow-path", default=None,
                    help="Path to workflow.yaml; validated internally "
                         "(default: .claude/workflow.yaml).")
    ap.add_argument("--linear-state", required=True,
                    help="The issue's current Linear column (after step 7).")
    ap.add_argument("--comments", required=True,
                    help="Path to the issue's comment list (JSON array).")
    ap.add_argument("--labels", default="",
                    help="Present label names: JSON file path or CSV string.")
    args = ap.parse_args()

    config = validate_workflow.load_config(args.workflow_config, args.workflow_path)

    raw_comments = _load_json(args.comments, "--comments")
    comments = parse_comments.coerce_comment_list(raw_comments, [])

    present_labels = _load_labels(args.labels)

    plan = route(config, args.linear_state, comments, present_labels)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
