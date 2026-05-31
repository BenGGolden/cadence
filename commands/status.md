---
description: Cadence status reporter — read-only Markdown view of every Linear issue currently in a workflow state. Shows current state, attempt count, lock and needs-human flags, plus per-state summary counts.
disable-model-invocation: true
---

# /cadence:status

You are the **Cadence status reporter**. **Run exactly once and exit.**
Do not loop. Do not write to Linear. Do not invoke any subagent. This
command is the human's at-a-glance view of the workflow — safe to run at
any time, from anywhere, with no side effects.

---

## Vocabulary

- **Workflow state**: a state defined in `.claude/workflow.yaml` under
  `states:`. Each has a `type` of `agent`, `gate`, or `terminal`.
- **Linear state**: a Linear board column. Workflow states declare their
  `linear_state`. A gate declares only its waiting `linear_state` —
  verdicts are signalled by the `cadence_approve` / `cadence_rework`
  labels (P4), not by additional columns.
- **Workflow Linear states**: the set of every `linear_state` plus
  `linear.pickup_state`. The same set `/cadence:tick` step 2 builds.
  Issues sitting in any of these columns are "in the workflow" for
  status purposes.
- **Verdict labels**: `label.cadence_approve` and `label.cadence_rework`.
  When an issue at a gate carries one of these labels, the next
  `/cadence:tick` fire will act on it. The summary highlights pending
  verdict labels so the human can see what is queued up.
- **Tracking comment**: a Linear comment whose body begins with
  `<!-- cadence:state`, `<!-- cadence:gate`, `<!-- cadence:reconcile`, or
  the legacy `<!-- stokowski:state` / `<!-- stokowski:gate` prefixes.
- **Attempt marker**: a `cadence:state` (or legacy `stokowski:state`)
  comment whose JSON has **no** `status` field. This is what
  `/cadence:tick`'s Route step (step 6) counts.
- **Failure record**: a `cadence:state` comment whose JSON includes
  `"status": "failed"`. Emitted on a subagent exception. **Not** an attempt
  marker — `/cadence:tick`'s Route step does not count it.

You only need one Linear MCP server connected to this session. Tool names
vary by vendor; commonly `mcp__linear__list_issues`,
`mcp__linear__get_issue`, `mcp__linear__list_comments`. Use whichever
verbs are present.

---

## Step 1 — Read and validate config

Invoke Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/validate_workflow.py --evidence`.

The script reads `.claude/workflow.yaml` and emits the full validated
config (and per-rule evidence under `--evidence`) as JSON on stdout.

- If it exits **1**, the YAML is missing or structurally unreadable —
  print the script's stderr verbatim and exit.
- If it exits **0 or 2**, parse the JSON on stdout. This is the **sole
  source of truth** for the config in this fire. Read from it:
  - `linear.team`, `linear.pickup_state` — required for the header.
  - `linear.project_slug` — optional; narrows the query to one project
    when set, otherwise the report is team-wide.
  - `label.cadence_active`, `label.cadence_needs_human` — required for
    the Lock and Needs-human columns.
  - `states`, `workflow_linear_states`, `linear_to_workflow`,
    `entry_state_name`, `entry_subagent`, `pickup_state` — used by
    steps 2-5.

  An exit code of **2** means one or more validation rules failed; this
  reporter still proceeds — a human often runs `/cadence:status` *because*
  the workflow is misconfigured. Keep the `evidence` array's `FAIL`
  blocks for the **Config warnings** section (step 5).

**Do not read `.claude/workflow.yaml` directly.** Reading the YAML
yourself produces a model-cacheable artifact that can go stale across
fires in the same conversation; re-invoking the script every fire is the
only way edits to the config are guaranteed to be picked up.

---

## Step 2 — Build the workflow-Linear-states set and reverse lookup

Read both structures directly from the validator output in step 1:

1. `workflowLinearStates` — `workflow_linear_states` from the validator
   (`linear.pickup_state`, then every state's `linear_state`). Used to
   filter the issue query in step 3.

2. `linearToWorkflow` — the `linear_to_workflow` map from the validator.
   Each entry is keyed by Linear column name and has the shape
   `{ "kind": "pickup" | "state" | "gate_waiting", "workflow_state": "<name>" | null, "linear_state_type": "agent" | "gate" | "terminal" | null }`.
   Duplicate Linear columns are caught by the validator's Rule 1
   (first-wins in the map either way); when the validator exit was 2,
   the Rule 1 `failure` already names the conflict for the **Config
   warnings** section.

---

## Step 3 — Query workflow issues

Using the Linear MCP, query the team named in `linear.team` (narrowed
to `linear.project_slug` when that field is present in the config) for
issues where Linear state ∈ `workflowLinearStates`. For each issue,
capture:

- `identifier` (e.g. `ENG-123`)
- `title`
- `state.name` (current Linear column)
- `priority` (numeric; null/"No priority" treated as worst)
- `updatedAt`
- `labels` (array of label names) — used to detect `cadence_active`,
  `cadence_needs_human`, and the two verdict labels (`cadence_approve` /
  `cadence_rework`) for the Verdict column on gate rows
- Any field your MCP exposes that identifies the issue for the comment
  fetch in step 4

If the query returns no issues, skip step 4 and go directly to step 5
with an empty result set.

**Performance note.** This is the slowest part of the report. If your
MCP supports pagination, paginate; if it supports server-side filtering
on multiple states, use it (one query covering all `workflowLinearStates`
is better than one per state).

---

## Step 4 — Fetch each issue's latest tracking comment

For **each** issue from step 3:

1. Query its comments via the Linear MCP and write them verbatim as a JSON
   array to `.cadence/comments.json` — call it `commentsFile`, reusing the
   one path and overwriting it per issue. (Step 1's `validate_workflow.py`
   already created `.cadence/` with a self-ignoring `.gitignore`, so this
   scratch file stays out of `git status`. Do not write it to the repo root
   or an OS temp directory.)
2. Determine the issue's workflow-state name from its `linearToWorkflow`
   entry (step 2). For a `pickup` entry there is no workflow state — use
   `entry_state_name` from step 1. Invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/parse_comments.py --input <commentsFile> --target-state <workflow-state name>`
3. From the JSON on stdout, read `attempt_count` and `latest_tracking_comment`
   (`kind`, `state`, `attempt`, `status`). Render the issue's **Attempt**
   column from `attempt_count`. Record `last_state` as
   `latest_tracking_comment.state`, or `(none yet)` when `attempt_count` is 0
   and `latest_tracking_comment.kind` is `null`. If `parse_errors` is
   non-empty, note it for the **Config warnings** section.

The script counts only `cadence:state` / legacy `stokowski:state` attempt
markers (failure records and gate / reconcile / sweep comments do not count
toward `attempt_count`).

If your MCP makes per-issue comment fetches expensive and the issue
count is high, you may degrade gracefully: skip the per-issue fetch and
render the Attempt column as `?`. Mention the degradation in the
**Config warnings** section. This is a fallback, not the default.

---

## Step 5 — Render the report

Write a JSON file to a temp path with the shape documented in the
`render_status_report.py` docstring. Fill it from the data you've
already gathered:

- `validator` — the verbatim JSON object from step 1 (must include
  `states`, `linear_to_workflow`, `linear`, `label`; include `evidence`
  when the validator exited 2).
- `issues` — for each issue from step 3, an object with `identifier`,
  `title`, `state_name` (the Linear column, i.e. `state.name`),
  `priority`, `updatedAt`, `labels`, `attempt_count` (from step 4; pass
  the string `"?"` when the per-issue fetch was degraded), and
  `last_state` (carried for callers but not rendered).
- `now` — current UTC time in ISO 8601 (resolve via `/cadence:sweep`
  step 2's recipe if you don't already have it).
- `team`, `project_slug`, `pickup_state` — from `validator.linear`.
- `degraded_issues` — list of identifiers whose per-issue comment fetch
  was degraded (per step 4); omit or pass an empty list when none.

Invoke Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/render_status_report.py --input <path-to-input-json>`.

Print the script's stdout verbatim. It contains the entire user-visible
report — header, issues table (or empty-set sentinel), per-state
summary, optional Concurrency table, optional Config warnings, and the
footer. No Linear writes have happened.

Workflow-state column formatting, verdict-cell rules, gate-bucket
collapsing, Concurrency `AT CAP` / `OVER CAP` thresholds, and the
footer line are all encoded in the script — see its docstring and tests
for the exact contract.

---

## Constraints

### Read-only

This command must **never** write to Linear. No label changes, no state
moves, no comments — not even tracking comments. If a step would
require a write to proceed, skip it and degrade the report.

### Error tolerance

- Missing or unreadable `.claude/workflow.yaml`: print error and exit.
- Linear MCP unavailable / unauthorised: print a single-line error
  noting the failure and exit. Do not pretend an empty result.
- Per-issue comment fetch fails for one issue: render its Attempt cell
  as `?` and continue with the rest.

### Performance

Status is typically the most-run Cadence command (humans check it
often). Aim for a single MCP query for issue list + one comment query
per issue. Do not invoke any subagent. Do not run Bash unless required
to resolve `now` (per `/cadence:sweep` step 2's resolution
recipe).

### Quoting and truncation

- Titles in tables: truncate at ~50 characters with a trailing `…`.
  Newlines in titles are replaced with single spaces.
- Identifiers are printed verbatim (Linear identifiers don't contain
  Markdown-breaking characters).
- The full `state.name` and `linear_state` strings are printed without
  truncation; if a Linear column name contains pipe characters, escape
  them as `\|` to keep the Markdown table well-formed.
