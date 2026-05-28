#!/usr/bin/env python3
"""Plan and filter Linear pickup candidates for Cadence /cadence:tick.

Caller(s):
  - commands/tick.md step 5 (pickup query plan + post-query candidate filter)

Failure modes eliminated:
  - Per-state scanning in prose: step 5 used to iterate the validator's
    `states` map looking for `max_in_flight` keys to decide which per-state
    queries to fire. Plan mode emits the list once.
  - Bounded reachability walk drift: P8.2 shipped a correctness fix
    specifically because the walk was prose. The walk now runs
    deterministically here.
  - Empty-candidates message drift: the `(caps reached for: <names>)`
    suffix was prose. The script renders the canonical message.

CLI:
  python filter_candidates.py --plan --workflow-config <path>
  python filter_candidates.py --workflow-config <path>
                              --candidates <path>
                              --in-flight <path>

Plan mode reads the validator's JSON output and emits the parameters for
the MCP pickup query plus one per-state count query for every state
declaring `max_in_flight`, also as JSON. Filter mode reads the
prose-returned pickup results and per-state counts and applies the
candidate filter, priority + createdAt sort, and bounded reachability
walk.

Examples for the default workflow
  (plan -> plan_review -> implement -> agent_review -> human_review -> done):

  - A `Todo` candidate's walk is `plan -> plan_review`. Caps on `plan`
    and `plan_review` bind; caps on `implement`, `agent_review`, or
    `human_review` do NOT affect this candidate.
  - A candidate at `plan_review` with `cadence_approve` has its walk
    start at `implement` and run `implement -> agent_review ->
    human_review`. Caps on `implement`, `agent_review`, and
    `human_review` all bind.
  - A candidate at `human_review` with `cadence_approve` has its walk
    start at `done` (terminal); no caps bind. With `cadence_rework` the
    walk is `implement -> agent_review -> human_review` (with
    `human_review` drain-exempt), so caps on `implement` and
    `agent_review` bind.

Input shapes (filter mode):
  --candidates  JSON array; each element is an MCP issue with these
                fields (extras are ignored):
                  identifier              str
                  current_linear_state    str
                  labels                  list (of names or {"name": ...}
                                          dicts; the GraphQL
                                          {"nodes": [...]} shape is also
                                          tolerated)
                  priority                int | null
                  createdAt               ISO 8601 str
                  blockers                optional list of blocker
                                          Linear-state strings
  --in-flight   JSON object mapping state_name -> int.

Output JSON (filter mode):
  {
    "ordered_identifiers": ["ENG-3", ...],
    "over_cap_states_that_blocked": ["plan_review"],
    "diagnostic_message": null | "No eligible issues.\\n(caps reached for: ...)"
  }
  `diagnostic_message` is non-null only when `ordered_identifiers` is
  empty; the parenthetical is omitted when no caps blocked anything.

Exit codes: 0 success; 1 bad / missing required input.
"""

import argparse
import json
import sys
from pathlib import Path

from _common import die


def _load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        die(f"Cadence: could not read {label} from {path}: {e}", 1)


def _label_names(labels):
    """Extract label name strings, tolerant of MCP shape variations."""
    if labels is None:
        return []
    if isinstance(labels, dict):
        labels = labels.get("nodes") or []
    if not isinstance(labels, list):
        return []
    names = []
    for entry in labels:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("title")
            if isinstance(name, str) and name:
                names.append(name)
        elif isinstance(entry, str) and entry:
            names.append(entry)
    return names


def _priority_rank(p):
    """Sort key piece: lower is higher priority.

    Linear priority numbers: 1=Urgent, 2=High, 3=Medium, 4=Low,
    0=No priority. Per tick.md step 5, null and "No priority" sort last.
    """
    if isinstance(p, bool) or p is None:
        return float("inf")
    if isinstance(p, int):
        return float("inf") if p == 0 else p
    return float("inf")


def _created_at_key(c):
    v = c.get("createdAt") or c.get("created_at") or ""
    return v if isinstance(v, str) else ""


def _build_plan(config):
    linear = config.get("linear") or {}
    states = config.get("states") or {}
    in_flight = []
    for name, body in states.items():
        if not isinstance(body, dict) or "max_in_flight" not in body:
            continue
        in_flight.append({
            "state_name": name,
            "linear_state": body.get("linear_state"),
        })
    project_slug = linear.get("project_slug")
    if not (isinstance(project_slug, str) and project_slug):
        project_slug = None
    return {
        "pickup_query": {
            "team": linear.get("team"),
            "project_slug": project_slug,
            "workflow_linear_states": config.get("workflow_linear_states") or [],
        },
        "in_flight_queries": in_flight,
    }


def _gate_state_by_linear(states):
    out = {}
    for name, body in states.items():
        if not isinstance(body, dict) or body.get("type") != "gate":
            continue
        ls = body.get("linear_state")
        if isinstance(ls, str) and ls:
            out[ls] = name
    return out


def _state_by_linear(states):
    out = {}
    for name, body in states.items():
        if not isinstance(body, dict):
            continue
        ls = body.get("linear_state")
        if isinstance(ls, str) and ls:
            out.setdefault(ls, name)
    return out


def _effective_target(candidate, *, pickup_state, entry_state, states,
                      gate_by_linear, state_by_linear,
                      label_approve, label_rework):
    """Return (target_state_name | None, drain_exempt_gate | None)."""
    col = candidate.get("current_linear_state")
    label_set = set(_label_names(candidate.get("labels")))
    has_approve = bool(label_approve) and label_approve in label_set
    has_rework = bool(label_rework) and label_rework in label_set

    if col == pickup_state:
        return entry_state, None

    if col in gate_by_linear:
        gate_name = gate_by_linear[col]
        gate_body = states.get(gate_name) or {}
        # Both labels present => treat as rework (matches tick.md "Both
        # verdict labels"). Rework precedence handles that here.
        if has_rework:
            return gate_body.get("on_rework"), gate_name
        if has_approve:
            return gate_body.get("on_approve"), gate_name
        return None, None

    return state_by_linear.get(col), None


def _walk_happy_path(target, states):
    """Bounded walk: include `target` and every subsequent agent state,
    plus the first gate or terminal reached.

    Stops at gate/terminal (inclusive), at any state missing from
    `states`, and on a re-visit (defence against happy-path cycles —
    the validator does not currently forbid them).
    """
    visited = []
    seen = set()
    current = target
    while (isinstance(current, str) and current
           and current in states and current not in seen):
        body = states[current]
        if not isinstance(body, dict):
            break
        visited.append(current)
        seen.add(current)
        if body.get("type") in ("gate", "terminal"):
            break
        nxt = body.get("next")
        if not isinstance(nxt, str):
            break
        current = nxt
    return visited


def _filter(config, candidates, in_flight_counts):
    states = config.get("states") or {}
    linear = config.get("linear") or {}
    label = config.get("label") or {}
    pickup_state = linear.get("pickup_state")
    entry_state = config.get("entry_state_name")
    workflow_linear_states = set(config.get("workflow_linear_states") or [])
    label_active = label.get("cadence_active")
    label_needs_human = label.get("cadence_needs_human")
    label_approve = label.get("cadence_approve")
    label_rework = label.get("cadence_rework")
    gate_by_linear = _gate_state_by_linear(states)
    state_by_linear_map = _state_by_linear(states)

    over_cap_set = set()
    for name, body in states.items():
        if not isinstance(body, dict) or "max_in_flight" not in body:
            continue
        cap = body.get("max_in_flight")
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            continue
        count = in_flight_counts.get(name, 0)
        if not isinstance(count, int) or isinstance(count, bool):
            count = 0
        if count >= cap:
            over_cap_set.add(name)

    pre_filtered = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        col = c.get("current_linear_state")
        if not isinstance(col, str) or col not in workflow_linear_states:
            continue
        labels_present = set(_label_names(c.get("labels")))
        if label_active and label_active in labels_present:
            continue
        if label_needs_human and label_needs_human in labels_present:
            continue
        blockers = c.get("blockers")
        if blockers is not None and isinstance(blockers, list):
            unresolved = [b for b in blockers
                          if isinstance(b, str) and b in workflow_linear_states]
            if unresolved:
                continue
        if col in gate_by_linear:
            has_approve = bool(label_approve) and label_approve in labels_present
            has_rework = bool(label_rework) and label_rework in labels_present
            if not (has_approve or has_rework):
                continue
        pre_filtered.append(c)

    pre_filtered.sort(key=lambda c: (_priority_rank(c.get("priority")),
                                     _created_at_key(c)))

    ordered = []
    blocked_states = []
    blocked_seen = set()

    for c in pre_filtered:
        target, drain_exempt = _effective_target(
            c, pickup_state=pickup_state, entry_state=entry_state,
            states=states, gate_by_linear=gate_by_linear,
            state_by_linear=state_by_linear_map,
            label_approve=label_approve, label_rework=label_rework,
        )
        if target is None:
            continue
        walk = _walk_happy_path(target, states)
        blockers = [s for s in walk
                    if s in over_cap_set and s != drain_exempt]
        if blockers:
            for b in blockers:
                if b not in blocked_seen:
                    blocked_seen.add(b)
                    blocked_states.append(b)
            continue
        ident = c.get("identifier")
        if isinstance(ident, str) and ident:
            ordered.append(ident)

    if ordered:
        diagnostic = None
    elif blocked_states:
        diagnostic = ("No eligible issues.\n"
                      f"(caps reached for: {', '.join(blocked_states)})")
    else:
        diagnostic = "No eligible issues."

    return {
        "ordered_identifiers": ordered,
        "over_cap_states_that_blocked": blocked_states,
        "diagnostic_message": diagnostic,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workflow-config", required=True,
                    help="Path to the validator's JSON output.")
    ap.add_argument("--plan", action="store_true",
                    help="Emit the query plan (pickup + per-state in-flight) "
                         "and exit.")
    ap.add_argument("--candidates",
                    help="Path to JSON array of pickup-query results.")
    ap.add_argument("--in-flight",
                    help="Path to JSON object {state_name: int}.")
    args = ap.parse_args()

    config = _load_json(args.workflow_config, "workflow config")
    if not isinstance(config, dict):
        die("Cadence: --workflow-config did not parse to a JSON object.", 1)

    if args.plan:
        out = _build_plan(config)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    if not args.candidates or not args.in_flight:
        die("Cadence: filter mode requires --candidates and --in-flight "
            "(or pass --plan to emit the query plan instead).", 1)

    candidates = _load_json(args.candidates, "candidates")
    if not isinstance(candidates, list):
        die("Cadence: --candidates must be a JSON array.", 1)
    in_flight = _load_json(args.in_flight, "in-flight counts")
    if not isinstance(in_flight, dict):
        die("Cadence: --in-flight must be a JSON object.", 1)

    out = _filter(config, candidates, in_flight)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
