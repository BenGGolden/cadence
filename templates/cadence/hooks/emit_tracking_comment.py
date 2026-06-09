#!/usr/bin/env python3
"""Produce canonical Cadence tracking-comment bodies.

Caller(s):
  - commands/tick.md step 7 (attempt marker)
  - commands/tick.md step 11 (waiting gate comment)
  - commands/tick.md Failure path (failure record)
  - templates/cadence/hooks/route_fire.py (imported; build_gate / build_reconcile
    produce the gate rework / gate escalation / reconcile comment bodies the
    router embeds in its plan)
  - commands/sweep.md step 5 (stale-lock sweep comment)

Failure mode eliminated:
  "JSON emission errors": tracking-comment bodies embed JSON, and an LLM that
  hand-writes invalid JSON poisons attempt counting on every later fire. This
  script builds the dict in Python and serialises it once, so the JSON is
  guaranteed well-formed and canonically spaced.

CLI:
  python emit_tracking_comment.py --kind {state|gate|reconcile|sweep|merge} [...]

Required args by kind:
  state      --state, plus --status OR (--attempt and --started-at)
  gate       --state and --status
  reconcile  --observed-linear-state, --expected-state, --reason
  sweep      --cleared-at, --last-activity, --stale-minutes,
             --threshold-minutes
  merge      --state and --status {merged|already_merged|failed|no_pr};
             --pr-url for merged/already_merged; --error for failed

Stdout: the full comment body (HTML-comment marker line + visible markdown).
Exit codes: 0 success; 1 bad / missing required input.
"""

import argparse
import json
import sys

from _common import die

ERROR_MAX_LEN = 400


def _clean_error(text):
    """Collapse newlines to spaces and truncate. json.dumps handles escaping."""
    if text is None:
        return None
    collapsed = " ".join(str(text).split())
    if len(collapsed) > ERROR_MAX_LEN:
        collapsed = collapsed[:ERROR_MAX_LEN]
    return collapsed


def _dumps(d):
    return json.dumps(d, ensure_ascii=False, separators=(", ", ": "))


def _emit(prefix, payload, visible):
    return f"<!-- cadence:{prefix} {_dumps(payload)} -->\n{visible}"


def build_state(args):
    if not args.state:
        die("Cadence: --state is required for --kind state.", 1)
    if args.status == "failed":
        attempt = args.attempt
        if attempt is None:
            die("Cadence: --attempt is required for a failure record.", 1)
        error = _clean_error(args.error) or ""
        payload = {"state": args.state, "attempt": attempt,
                   "status": "failed", "error": error}
        subagent = args.subagent or "subagent"
        visible = (f"**[Cadence]** Subagent **{subagent}** failed at "
                   f"attempt {attempt}: {error}")
        return _emit("state", payload, visible)
    if args.status:
        die(f"Cadence: --kind state only supports --status failed (got "
            f"'{args.status}').", 1)
    # Attempt marker.
    if args.attempt is None or not args.started_at:
        die("Cadence: --attempt and --started-at are required for an "
            "attempt marker (--kind state with no --status).", 1)
    payload = {"state": args.state, "attempt": args.attempt,
               "started_at": args.started_at}
    visible = (f"**[Cadence]** Entering state: **{args.state}** "
               f"(attempt {args.attempt})")
    return _emit("state", payload, visible)


def build_gate(args):
    if not args.state:
        die("Cadence: --state is required for --kind gate.", 1)
    if not args.status:
        die("Cadence: --status is required for --kind gate.", 1)
    if args.status == "waiting":
        payload = {"state": args.state, "status": "waiting"}
        visible = f"**[Cadence]** Awaiting human review at **{args.state}**."
    elif args.status == "rework":
        if not args.rework_to:
            die("Cadence: --rework-to is required for a rework gate comment.", 1)
        payload = {"state": args.state, "status": "rework",
                   "rework_to": args.rework_to}
        visible = (f"**[Cadence]** Rework requested; routing to "
                   f"**{args.rework_to}** (attempt counts toward "
                   f"{args.rework_to}'s max_attempts).")
    elif args.status == "escalated":
        payload = {"state": args.state, "status": "escalated"}
        visible = (f"**[Cadence]** Rework limit reached at gate "
                   f"**{args.state}**. Needs human intervention.")
    else:
        die(f"Cadence: --kind gate does not support --status '{args.status}'.", 1)
    return _emit("gate", payload, visible)


def build_merge(args):
    if not args.state:
        die("Cadence: --state is required for --kind merge.", 1)
    if not args.status:
        die("Cadence: --status is required for --kind merge.", 1)
    if args.status in ("merged", "already_merged"):
        if not args.pr_url:
            die(f"Cadence: --pr-url is required for a "
                f"'{args.status}' merge comment.", 1)
        payload = {"state": args.state, "status": args.status,
                   "pr_url": args.pr_url}
        if args.status == "merged":
            visible = f"**[Cadence]** Merged PR {args.pr_url}; advancing."
        else:
            visible = (f"**[Cadence]** PR {args.pr_url} was already merged; "
                       f"advancing.")
    elif args.status == "failed":
        error = _clean_error(args.error) or ""
        payload = {"state": args.state, "status": "failed", "error": error}
        visible = (f"**[Cadence]** PR merge failed: {error}. "
                   f"Needs human intervention.")
    elif args.status == "no_pr":
        payload = {"state": args.state, "status": "no_pr"}
        visible = (f"**[Cadence]** Approved at gate **{args.state}** but no PR "
                   f"URL was found in the issue history; cannot merge. Needs "
                   f"human intervention.")
    else:
        die(f"Cadence: --kind merge does not support --status "
            f"'{args.status}'.", 1)
    return _emit("merge", payload, visible)


def build_sweep(args):
    missing = [name for name, val in (
        ("--cleared-at", args.cleared_at),
        ("--last-activity", args.last_activity),
    ) if not val]
    if args.stale_minutes is None:
        missing.append("--stale-minutes")
    if args.threshold_minutes is None:
        missing.append("--threshold-minutes")
    if missing:
        die(f"Cadence: --kind sweep requires {', '.join(missing)}.", 1)
    payload = {
        "cleared_at": args.cleared_at,
        "last_activity": args.last_activity,
        "stale_minutes": args.stale_minutes,
    }
    visible = (f"**[Cadence]** Stale lock cleared (last activity "
               f"{args.last_activity}, {args.stale_minutes} minutes ago, "
               f"threshold {args.threshold_minutes} minutes).")
    return _emit("sweep", payload, visible)


def build_reconcile(args):
    missing = [name for name, val in (
        ("--observed-linear-state", args.observed_linear_state),
        ("--expected-state", args.expected_state),
        ("--reason", args.reason),
    ) if not val]
    if missing:
        die(f"Cadence: --kind reconcile requires {', '.join(missing)}.", 1)
    payload = {
        "observed_linear_state": args.observed_linear_state,
        "expected_state": args.expected_state,
        "reason": args.reason,
    }
    visible = ("**[Cadence]** Detected human-driven state change; "
               "proceeding from Linear's state.")
    return _emit("reconcile", payload, visible)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", required=True,
                    choices=["state", "gate", "reconcile", "sweep", "merge"])
    ap.add_argument("--state")
    ap.add_argument("--attempt", type=int)
    ap.add_argument("--started-at")
    ap.add_argument("--status", choices=["failed", "waiting", "rework",
                                         "escalated", "merged",
                                         "already_merged", "no_pr"])
    ap.add_argument("--pr-url", help="PR URL for --kind merge.")
    ap.add_argument("--error")
    ap.add_argument("--subagent", help="Subagent name for a failure record's "
                                       "visible line.")
    ap.add_argument("--rework-to")
    ap.add_argument("--from", dest="from_state")
    ap.add_argument("--observed-linear-state")
    ap.add_argument("--expected-state")
    ap.add_argument("--reason")
    ap.add_argument("--cleared-at",
                    help="UTC ISO 8601 timestamp the sweeper cleared the "
                         "lock at (--kind sweep).")
    ap.add_argument("--last-activity",
                    help="Issue's last updatedAt timestamp (--kind sweep).")
    ap.add_argument("--stale-minutes", type=int,
                    help="Integer minutes between --last-activity and "
                         "--cleared-at (--kind sweep).")
    ap.add_argument("--threshold-minutes", type=int,
                    help="Configured stale-after-minutes threshold "
                         "(--kind sweep).")
    args = ap.parse_args()

    if args.kind == "state":
        body = build_state(args)
    elif args.kind == "gate":
        body = build_gate(args)
    elif args.kind == "sweep":
        body = build_sweep(args)
    elif args.kind == "merge":
        body = build_merge(args)
    else:
        body = build_reconcile(args)

    print(body)
    sys.exit(0)


if __name__ == "__main__":
    main()
