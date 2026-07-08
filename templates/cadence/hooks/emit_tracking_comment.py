#!/usr/bin/env python3
"""Produce canonical Cadence tracking-comment bodies.

Caller(s):
  - commands/tick.md step 7 (attempt marker)
  - commands/tick.md step 11 (waiting gate comment)
  - commands/tick.md Failure path (failure record)
  - templates/cadence/hooks/route_fire.py (imported; build_gate / build_reconcile
    produce the gate rework / gate escalation / reconcile comment bodies the
    router embeds in its plan)
  - templates/cadence/hooks/classify_merge.py (imported; build_merge produces the
    merged / already_merged / failed / no_pr comment bodies for the tick.md
    merge-on-approve sub-phase)
  - commands/sweep.md step 5 (stale-lock sweep comment)

Failure mode eliminated:
  "JSON emission errors": tracking-comment bodies embed JSON, and an LLM that
  hand-writes invalid JSON poisons attempt counting on every later fire. This
  script builds the dict in Python and serialises it once, so the JSON is
  guaranteed well-formed and canonically spaced.

CLI:
  python emit_tracking_comment.py
    --kind {state|gate|reconcile|sweep|merge|warning} [...]

Required args by kind:
  state      --state, plus --status OR (--attempt and --started-at)
  gate       --state and --status
  reconcile  --observed-linear-state, --expected-state, --reason
  sweep      --cleared-at, --last-activity, --stale-minutes,
             --threshold-minutes
  merge      --state and --status {merged|already_merged|failed|no_pr};
             --pr-url for merged/already_merged; --error for failed
  warning    --warning-file (a JSON object {parent, chars, message} written by
             compose_lifecycle_context.py on an oversized-parent soft-budget
             warn). Produces an informational `cadence:warning` comment; unlike
             the other kinds it is never counted or treated as a boundary.

Stdout: the full comment body (HTML-comment marker line + visible markdown).
Exit codes: 0 success; 1 bad / missing required input.
"""

import argparse
import io
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


def build_warning(args):
    """Informational context-warning comment (oversized inherited parent spec).

    The payload is a JSON object compose_lifecycle_context.py wrote to
    --warning-file on a soft-budget warn: {parent, chars, message}. The marker
    carries the structured facts; the visible line is the guidance message the
    compose step already composed (it knows the configured threshold)."""
    if not args.warning_file:
        die("Cadence: --warning-file is required for --kind warning.", 1)
    try:
        with open(args.warning_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        die(f"Cadence: could not read --warning-file "
            f"{args.warning_file}: {e}", 1)
    if not isinstance(data, dict):
        die("Cadence: --warning-file must hold a JSON object.", 1)
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        die("Cadence: --warning-file is missing a non-empty 'message'.", 1)
    payload = {"parent": data.get("parent"), "chars": data.get("chars")}
    visible = f"**[Cadence]** ⚠️ {message}"
    return _emit("warning", payload, visible)


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
    # The warning kind's visible line carries a ⚠️; force UTF-8 so it does not
    # crash on Windows when stdout defaults to cp1252 (matches
    # compose_lifecycle_context.py / route_fire.py). Done here in main() — not
    # at module level — because route_fire.py / classify_merge.py import this
    # module for its formatters and must not have their stdout replaced.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      newline="")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", required=True,
                    choices=["state", "gate", "reconcile", "sweep", "merge",
                             "warning"])
    ap.add_argument("--state")
    ap.add_argument("--attempt", type=int)
    ap.add_argument("--started-at")
    ap.add_argument("--status", choices=["failed", "waiting", "rework",
                                         "escalated", "merged",
                                         "already_merged", "no_pr"])
    ap.add_argument("--pr-url", help="PR URL for --kind merge.")
    ap.add_argument("--warning-file",
                    help="Path to the JSON payload for --kind warning "
                         "(compose_lifecycle_context.py wrote it).")
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
    elif args.kind == "warning":
        body = build_warning(args)
    else:
        body = build_reconcile(args)

    print(body)
    sys.exit(0)


if __name__ == "__main__":
    main()
