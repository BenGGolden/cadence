#!/usr/bin/env python3
"""Render the full /cadence:status Markdown report.

Caller(s):
  - commands/status.md step 5 (the entire report — header, issue table,
    per-state summary, optional Concurrency table, Config warnings,
    footer).

Failure modes eliminated:
  - Templating drift: status.md step 5 was ~120 lines of prose describing
    Markdown table headers, per-state summary lines, gate verdict
    breakdown, concurrency cells, and a footer. The same shape every
    run; prose edits silently changed the contract callers depend on.
  - Per-state derivation drift: the workflow-state column lookup is now
    a single read of `linear_to_workflow` from the validator (P2). The
    in-flight counts feed the per-state summary AND the Concurrency
    table from one pass over the issue list.
  - Gate-verdict bucket drift: the four-way breakdown (awaiting / 👍 /
    👎 / ⚠️ both labels) is now deterministic.

CLI:
  python render_status_report.py --input <path-to-input-json>

The input JSON shape (the bootstrap composes this from step 1's
validator output + step 3/4's issue gather):

  {
    "validator": <verbatim validator-output dict, must include `states`,
                  `linear_to_workflow`, `linear`, `label`; may include
                  `evidence` when the validator exited 2>,
    "issues": [
      {
        "identifier": "ENG-1",
        "title": "...",
        "state_name": "Implementing",   // Linear column (state.name)
        "priority": 2,                  // int | null
        "updatedAt": "2026-05-28T...",  // ISO 8601 str
        "labels": ["..."],              // list of names or {"name":...} dicts
        "attempt_count": 1,             // int or "?" (degraded)
        "last_state": "implement"       // optional; carried for callers but
                                        // not rendered in the visible report
      }, ...
    ],
    "now": "2026-05-28T12:00:00Z",
    "team": "ENG",
    "project_slug": "cadence" | null,
    "pickup_state": "Todo",
    "degraded_issues": ["ENG-2", ...]   // optional; per-issue parse degraded
                                        // or returned parse_errors
  }

Stdout: the full Markdown report to print verbatim under /cadence:status.

Exit codes:
  0  success
  1  bad / missing required input
"""

import argparse
import io
import json
import sys
from pathlib import Path

from _common import die

# The report uses non-ASCII glyphs (🔒, 🛑, 👍, 👎, ⚠️, →, —, …). On
# Windows the default stdout encoding is cp1252 and emitting these would
# crash. Force UTF-8 regardless of the parent process's locale.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  newline="")

TITLE_TRUNCATE = 50  # ~50 chars per status.md "Quoting and truncation"


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
    out = []
    for entry in labels:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("title")
            if isinstance(name, str) and name:
                out.append(name)
        elif isinstance(entry, str) and entry:
            out.append(entry)
    return out


def _priority_rank(p):
    if isinstance(p, bool) or p is None:
        return float("inf")
    if isinstance(p, int):
        return float("inf") if p == 0 else p
    return float("inf")


def _escape_cell(value):
    """Escape a Markdown table cell: replace newlines with spaces, escape
    pipes."""
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    s = s.replace("|", "\\|")
    return s


def _truncate_title(title):
    if not isinstance(title, str):
        return ""
    flat = title.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if len(flat) <= TITLE_TRUNCATE:
        return flat
    return flat[:TITLE_TRUNCATE - 1] + "…"


def _format_workflow_state_cell(state_name, linear_to_workflow):
    """Map a Linear column (state.name) to the Workflow state cell.

    `pickup` -> "(pickup)"
    `state`  -> the workflow state name
    `gate_waiting` -> "<gate> (waiting)"
    Anything not in the map -> "(unknown)" (defensive; the bootstrap's
    step 3 already filtered to workflow_linear_states).
    """
    entry = linear_to_workflow.get(state_name)
    if not isinstance(entry, dict):
        return "(unknown)"
    kind = entry.get("kind")
    name = entry.get("workflow_state")
    if kind == "pickup":
        return "(pickup)"
    if kind == "gate_waiting":
        return f"{name} (waiting)" if name else "(waiting)"
    if kind == "state":
        return name or "(unknown)"
    return "(unknown)"


def _format_attempt(value):
    if isinstance(value, str):
        return value if value else "—"
    if isinstance(value, bool) or not isinstance(value, int):
        return "—"
    if value <= 0:
        return "—"
    return str(value)


def _verdict_cell(state_name, labels_set, linear_to_workflow,
                  label_approve, label_rework):
    entry = linear_to_workflow.get(state_name)
    if not isinstance(entry, dict) or entry.get("kind") != "gate_waiting":
        return ""
    has_approve = bool(label_approve) and label_approve in labels_set
    has_rework = bool(label_rework) and label_rework in labels_set
    if has_approve and has_rework:
        return "both (→ rework)"
    if has_approve:
        return "cadence-approve"
    if has_rework:
        return "cadence-rework"
    return ""


def _sort_issues(issues):
    """Priority ascending, then updatedAt descending. Stable on ties.

    Stable two-pass sort: first by updatedAt descending (the secondary
    key), then by priority ascending (the primary). Python's sort is
    stable, so the secondary order is preserved within priority groups.
    """
    by_updated = sorted(
        issues,
        key=lambda i: i.get("updatedAt") if isinstance(i.get("updatedAt"), str) else "",
        reverse=True,
    )
    return sorted(by_updated, key=lambda i: _priority_rank(i.get("priority")))


def _render_header(now, team, project_slug, pickup_state):
    project_display = project_slug if (isinstance(project_slug, str)
                                       and project_slug) else "(any)"
    lines = [
        f"## Cadence status — {now}",
        "",
        (f"Team: **{team}**   Project: **{project_display}**   "
         f"Pickup: **{pickup_state}**"),
        "",
        "### Issues in workflow",
        "",
    ]
    return "\n".join(lines)


def _render_issue_table(issues, *, linear_to_workflow, label_active,
                        label_needs_human, label_approve, label_rework):
    if not issues:
        return "*No issues currently in workflow states.*"
    header = (
        "| ID | Title | Linear column | Workflow state | Attempt | Lock "
        "| Needs human | Verdict |\n"
        "|----|-------|---------------|----------------|---------|------"
        "|-------------|---------|"
    )
    rows = [header]
    for i in issues:
        labels_set = set(_label_names(i.get("labels")))
        identifier = _escape_cell(i.get("identifier"))
        title = _escape_cell(_truncate_title(i.get("title")))
        col = _escape_cell(i.get("state_name"))
        workflow = _escape_cell(_format_workflow_state_cell(
            i.get("state_name"), linear_to_workflow))
        attempt = _format_attempt(i.get("attempt_count"))
        lock = "\U0001F512" if (label_active and label_active in labels_set) else ""
        needs = "\U0001F6D1" if (label_needs_human and label_needs_human in labels_set) else ""
        verdict = _verdict_cell(i.get("state_name"), labels_set,
                                linear_to_workflow, label_approve,
                                label_rework)
        rows.append(
            f"| {identifier} | {title} | {col} | {workflow} | {attempt} "
            f"| {lock} | {needs} | {verdict} |"
        )
    return "\n".join(rows)


def _issues(n):
    """English-pluralised "N issue(s)" — keeps the single-/multi-issue
    forms readable in both the per-state summary and the gate buckets."""
    return f"{n} issue" if n == 1 else f"{n} issues"


def _count_issues_by_column(issues):
    counts = {}
    for i in issues:
        col = i.get("state_name")
        if not isinstance(col, str) or not col:
            continue
        counts[col] = counts.get(col, 0) + 1
    return counts


def _bucket_gate_issues(issues_at_gate, label_approve, label_rework):
    """For issues at a single gate's waiting column, classify into the
    four buckets used by the per-state summary."""
    awaiting = approve = rework = both = 0
    for i in issues_at_gate:
        labels_set = set(_label_names(i.get("labels")))
        has_a = bool(label_approve) and label_approve in labels_set
        has_r = bool(label_rework) and label_rework in labels_set
        if has_a and has_r:
            both += 1
        elif has_a:
            approve += 1
        elif has_r:
            rework += 1
        else:
            awaiting += 1
    return awaiting, approve, rework, both


def _render_per_state_section(states, issues, *, pickup_state,
                              label_active, label_needs_human,
                              label_approve, label_rework):
    issues_by_col = {}
    for i in issues:
        col = i.get("state_name")
        if not isinstance(col, str) or not col:
            continue
        issues_by_col.setdefault(col, []).append(i)

    lines = ["", "### Per-state counts", ""]

    def _suffix_counts(at_col):
        suffix = ""
        if label_active:
            locked = sum(1 for i in at_col
                         if label_active in set(_label_names(i.get("labels"))))
            if locked:
                suffix += f"   \U0001F512 {locked} locked"
        if label_needs_human:
            nh = sum(1 for i in at_col
                     if label_needs_human in set(_label_names(i.get("labels"))))
            if nh:
                suffix += f"   \U0001F6D1 {nh} needs-human"
        return suffix

    for name, body in states.items():
        if not isinstance(body, dict):
            continue
        linear_state = body.get("linear_state")
        if not isinstance(linear_state, str) or not linear_state:
            continue
        at_col = issues_by_col.get(linear_state, [])
        count = len(at_col)
        stype = body.get("type")
        if stype == "gate":
            head = (f"- **{name}** (gate, `{linear_state}`) "
                    f"— {_issues(count)}")
            if count == 0:
                lines.append(head)
                continue
            awaiting, approve, rework, both = _bucket_gate_issues(
                at_col, label_approve, label_rework)
            if awaiting == count:
                lines.append(head)
                continue
            lines.append(head)
            if awaiting:
                lines.append(f"  - awaiting verdict — {_issues(awaiting)}")
            if approve:
                lines.append(f"  - \U0001F44D cadence-approve — {_issues(approve)}")
            if rework:
                lines.append(f"  - \U0001F44E cadence-rework — {_issues(rework)}")
            if both:
                lines.append(f"  - ⚠️ both labels (treated as rework) "
                             f"— {_issues(both)}")
        else:
            suffix = _suffix_counts(at_col)
            lines.append(f"- **{name}** (`{linear_state}`) "
                         f"— {_issues(count)}{suffix}")

    pickup_count = len(issues_by_col.get(pickup_state, []))
    lines.append(f"- **(pickup)** (`{pickup_state}`) — "
                 f"{_issues(pickup_count)}")

    return "\n".join(lines)


def _any_max_in_flight(states):
    for body in states.values():
        if isinstance(body, dict) and "max_in_flight" in body:
            return True
    return False


def _render_concurrency_section(states, issues):
    if not _any_max_in_flight(states):
        return ""

    by_col = _count_issues_by_column(issues)

    lines = [
        "",
        "### Concurrency",
        "",
        "| State | In flight | Cap | Status |",
        "|-------|-----------|-----|--------|",
    ]
    for name, body in states.items():
        if not isinstance(body, dict):
            continue
        stype = body.get("type")
        linear_state = body.get("linear_state")
        if not isinstance(linear_state, str) or not linear_state:
            continue
        in_flight = by_col.get(linear_state, 0)

        if stype == "gate":
            state_cell = f"{name} (gate)"
        elif stype == "terminal":
            state_cell = f"{name} (terminal)"
        else:
            state_cell = name

        if stype == "terminal":
            cap_cell = "n/a"
            status_cell = ""
        elif "max_in_flight" in body:
            cap = body.get("max_in_flight")
            if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
                cap_cell = "(none)"
                status_cell = ""
            else:
                cap_cell = str(cap)
                if in_flight > cap:
                    status_cell = "OVER CAP"
                elif in_flight == cap:
                    status_cell = "AT CAP"
                else:
                    status_cell = ""
        else:
            cap_cell = "(none)"
            status_cell = ""

        lines.append(
            f"| {_escape_cell(state_cell)} | {in_flight} | "
            f"{_escape_cell(cap_cell)} | {_escape_cell(status_cell)} |"
        )
    return "\n".join(lines)


def _render_config_warnings(validator, degraded_issues):
    failures = []
    evidence = validator.get("evidence") if isinstance(validator, dict) else None
    if isinstance(evidence, list):
        for ev in evidence:
            if isinstance(ev, dict) and ev.get("result") == "FAIL":
                failures.append(ev)

    degraded = [d for d in (degraded_issues or [])
                if isinstance(d, str) and d]

    if not failures and not degraded:
        return ""

    lines = ["", "### Config warnings", ""]
    for ev in failures:
        rule = ev.get("rule")
        title = ev.get("title") or ""
        failure = ev.get("failure") or ""
        lines.append(f"- **Rule {rule} ({title})**: {failure}")
    if degraded:
        if failures:
            lines.append("")
        lines.append(
            f"Comment fetch degraded for: {', '.join(degraded)}."
        )
    return "\n".join(lines)


def _render_footer():
    return "\nRead-only — no Linear writes performed."


def render(payload):
    validator = payload.get("validator") or {}
    if not isinstance(validator, dict):
        die("Cadence: --input.validator must be an object.", 1)
    states = validator.get("states") or {}
    if not isinstance(states, dict):
        die("Cadence: --input.validator.states must be an object.", 1)
    linear_to_workflow = validator.get("linear_to_workflow") or {}
    if not isinstance(linear_to_workflow, dict):
        die("Cadence: --input.validator.linear_to_workflow must be an object.",
            1)
    label = validator.get("label") or {}
    label_active = label.get("cadence_active") if isinstance(label, dict) else None
    label_needs_human = label.get("cadence_needs_human") if isinstance(label, dict) else None
    label_approve = label.get("cadence_approve") if isinstance(label, dict) else None
    label_rework = label.get("cadence_rework") if isinstance(label, dict) else None

    issues = payload.get("issues") or []
    if not isinstance(issues, list):
        die("Cadence: --input.issues must be an array.", 1)
    issues = [i for i in issues if isinstance(i, dict)]
    sorted_issues = _sort_issues(issues)

    now = payload.get("now") or ""
    team = payload.get("team") or ""
    project_slug = payload.get("project_slug")
    pickup_state = payload.get("pickup_state") or ""

    parts = [_render_header(now, team, project_slug, pickup_state)]
    parts.append(_render_issue_table(
        sorted_issues,
        linear_to_workflow=linear_to_workflow,
        label_active=label_active,
        label_needs_human=label_needs_human,
        label_approve=label_approve,
        label_rework=label_rework,
    ))
    parts.append(_render_per_state_section(
        states, issues,
        pickup_state=pickup_state,
        label_active=label_active,
        label_needs_human=label_needs_human,
        label_approve=label_approve,
        label_rework=label_rework,
    ))
    concurrency = _render_concurrency_section(states, issues)
    if concurrency:
        parts.append(concurrency)
    warnings = _render_config_warnings(validator,
                                       payload.get("degraded_issues"))
    if warnings:
        parts.append(warnings)
    parts.append(_render_footer())

    return "\n".join(parts) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True,
                    help="Path to the JSON input file (see module "
                         "docstring for shape).")
    args = ap.parse_args()

    payload = _load_json(args.input, "--input")
    if not isinstance(payload, dict):
        die("Cadence: --input must parse to a JSON object.", 1)

    out = render(payload)
    sys.stdout.write(out)
    sys.exit(0)


if __name__ == "__main__":
    main()
