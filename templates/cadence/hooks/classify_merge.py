#!/usr/bin/env python3
"""Decide the outcome of a Cadence `merge_on_approve` gate-approve fire.

Caller:
  - commands/tick.md "Merge on approve" sub-phase (replaces the model-executed
    five-branch outcome table that used to run inline in dispatch prose).

Pure decision core — no I/O, no MCP, no shell-out. When a terminal gate
declares `merge_on_approve: true` and a human approves, the bootstrap must read
the PR, decide among a handful of outcomes, and pick a *different* combination
of side effects for each (post which comment, change which labels, whether to
advance to the terminal). That branch-selection is the most consequential in
the whole tick: getting it wrong means advancing to **Done** on a PR that never
merged, or leaving a soft lock in place. This module makes the choice
deterministic and unit-tested; the two GitHub MCP calls
(`get_pull_request` / `merge_pull_request`) stay in the bootstrap, which remains
the sole Linear/GitHub writer and applies every action this module returns.

The decision happens at two points, one function each:
  - classify_after_read   — after `get_pull_request` (or its failure / a null
                            PR URL): no_pr / already_merged / attempt_merge /
                            escalate.
  - classify_after_merge  — after `merge_pull_request` (or its failure):
                            merged / failed.

Comment bodies are produced by emit_tracking_comment.build_merge, never
re-templated here (mirrors how route_fire imports build_gate / build_reconcile).

CLI:
  python classify_merge.py --phase read
    --pr-url <url|"">
    [--pr-state-json <file> | --read-error "<text>"]
    --state <gate> --merge-target <column>
    [--workflow-config <validatorJson> | --workflow-path <workflow.yaml>]

  python classify_merge.py --phase merge
    --pr-url <url>
    [--merge-result-json <file> | --merge-error "<text>"]
    --state <gate> --merge-target <column>
    [--workflow-config <validatorJson> | --workflow-path <workflow.yaml>]

Stdout: the plan as JSON — {"decision": str, "actions": [action dict, ...]}.
Exit codes:
  0  plan emitted on stdout
  1  bad / missing required input (config unreadable, unknown --phase, etc.)
"""

import argparse
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import emit_tracking_comment
import validate_workflow

# Comment bodies and Linear column names may carry non-ASCII characters; force
# UTF-8 so stdout is stable regardless of the parent locale (Windows defaults
# to cp1252).
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


# ----- comment-body wrapper (imported, never re-templated) ------------------

def _merge_body(state, status, pr_url=None, error=None):
    ns = SimpleNamespace(state=state, status=status, pr_url=pr_url, error=error)
    return emit_tracking_comment.build_merge(ns)


# ----- the decisions -------------------------------------------------------

def classify_after_read(pr_url, pr_state, read_error, labels,
                        gate_state, merge_target):
    """Return {"decision": str, "actions": [action dict, ...]}.

    pr_url        implementer PR URL from the plan (str | "" | None)
    pr_state      GitHub get_pull_request result dict ({"state","merged",...})
                  or None
    read_error    error text if get_pull_request errored (str | None);
                  mutually exclusive with pr_state
    labels        validator config's `label` block; needs "cadence_active"
                  and "cadence_needs_human"
    gate_state    plan.matched_state — the gate name, for comment bodies (str)
    merge_target  plan.merge_target_linear_state — terminal column (str)

    decision is one of: "no_pr" | "already_merged" | "attempt_merge" | "escalate"
    """
    labels = labels if isinstance(labels, dict) else {}
    active = labels.get("cadence_active")
    needs_human = labels.get("cadence_needs_human")

    # 1 — no PR URL: escalate before ever inspecting PR state.
    if not pr_url:
        return {
            "decision": "no_pr",
            "actions": [
                _post_comment(_merge_body(gate_state, "no_pr")),
                _add_label(needs_human),
                _remove_label(active),
            ],
        }

    # 2 — the PR read errored.
    if read_error:
        return {
            "decision": "escalate",
            "actions": [
                _post_comment(_merge_body(gate_state, "failed", error=read_error)),
                _add_label(needs_human),
                _remove_label(active),
            ],
        }

    # 3 — unusable PR state (no dict to inspect).
    if not isinstance(pr_state, dict):
        return {
            "decision": "escalate",
            "actions": [
                _post_comment(_merge_body(gate_state, "failed",
                                          error="could not read PR state")),
                _add_label(needs_human),
                _remove_label(active),
            ],
        }

    # 4 — already merged (e.g. a human merged manually): advance.
    if bool(pr_state.get("merged")):
        return {
            "decision": "already_merged",
            "actions": [
                _post_comment(_merge_body(gate_state, "already_merged",
                                          pr_url=pr_url)),
                _move_state(merge_target),
                _remove_label(active),
            ],
        }

    # 5 — open and unmerged: the bootstrap should attempt the merge. No actions
    #     yet — the lock release is deferred to the merge phase.
    if pr_state.get("state") == "open":
        return {"decision": "attempt_merge", "actions": []}

    # 6 — otherwise (closed & not merged, an abandoned PR, etc.): escalate.
    return {
        "decision": "escalate",
        "actions": [
            _post_comment(_merge_body(
                gate_state, "failed",
                error=f"PR is {pr_state.get('state')!r} but not merged")),
            _add_label(needs_human),
            _remove_label(active),
        ],
    }


def classify_after_merge(pr_url, merge_result, merge_error, labels,
                         gate_state, merge_target):
    """Return {"decision": str, "actions": [...]}.

    merge_result  GitHub merge_pull_request result dict ({"merged","message",...})
                  or None
    merge_error   error text if merge_pull_request errored (str | None)

    decision is one of: "merged" | "failed"
    (other params as in classify_after_read)
    """
    labels = labels if isinstance(labels, dict) else {}
    active = labels.get("cadence_active")
    needs_human = labels.get("cadence_needs_human")

    # 1 — the merge call errored.
    if merge_error:
        return {
            "decision": "failed",
            "actions": [
                _post_comment(_merge_body(gate_state, "failed",
                                          error=merge_error)),
                _add_label(needs_human),
                _remove_label(active),
            ],
        }

    # 2 — merge completed: advance.
    if isinstance(merge_result, dict) and bool(merge_result.get("merged")):
        return {
            "decision": "merged",
            "actions": [
                _post_comment(_merge_body(gate_state, "merged", pr_url=pr_url)),
                _move_state(merge_target),
                _remove_label(active),
            ],
        }

    # 3 — call returned but the PR did not merge: escalate.
    reason = None
    if isinstance(merge_result, dict):
        reason = merge_result.get("message")
    return {
        "decision": "failed",
        "actions": [
            _post_comment(_merge_body(gate_state, "failed",
                                      error=reason or "merge did not complete")),
            _add_label(needs_human),
            _remove_label(active),
        ],
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", required=True, choices=["read", "merge"],
                    help="Which decision point to evaluate.")
    ap.add_argument("--pr-url", default="",
                    help="Implementer PR URL from the plan (\"\" when none).")
    ap.add_argument("--pr-state-json", default=None,
                    help="Path to the get_pull_request result JSON (--phase read).")
    ap.add_argument("--read-error", default=None,
                    help="get_pull_request error text (--phase read).")
    ap.add_argument("--merge-result-json", default=None,
                    help="Path to the merge_pull_request result JSON "
                         "(--phase merge).")
    ap.add_argument("--merge-error", default=None,
                    help="merge_pull_request error text (--phase merge).")
    ap.add_argument("--state", required=True,
                    help="The gate name (plan.matched_state), for comment bodies.")
    ap.add_argument("--merge-target", required=True,
                    help="Terminal Linear column (plan.merge_target_linear_state).")
    ap.add_argument("--workflow-config", default=None,
                    help="Path to a pre-built validator JSON dict (dry-run/tests).")
    ap.add_argument("--workflow-path", default=None,
                    help="Path to workflow.yaml; validated internally "
                         "(default: .claude/workflow.yaml).")
    args = ap.parse_args()

    config = validate_workflow.load_config(args.workflow_config,
                                           args.workflow_path)
    labels = config.get("label") or {}

    if args.phase == "read":
        pr_state = None
        if args.pr_state_json:
            pr_state = _load_json(args.pr_state_json, "--pr-state-json")
        result = classify_after_read(
            args.pr_url, pr_state, args.read_error, labels,
            args.state, args.merge_target)
    else:  # merge
        merge_result = None
        if args.merge_result_json:
            merge_result = _load_json(args.merge_result_json,
                                      "--merge-result-json")
        result = classify_after_merge(
            args.pr_url, merge_result, args.merge_error, labels,
            args.state, args.merge_target)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
