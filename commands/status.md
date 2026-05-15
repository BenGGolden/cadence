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
  `linear_state`; gates additionally declare `approved_linear_state` and
  `rework_linear_state`.
- **Workflow Linear states**: the set of every `linear_state`, every
  gate's `approved_linear_state`, every gate's `rework_linear_state`,
  plus `linear.pickup_state`. The same set `/cadence:tick`
  step 4 builds. Issues sitting in any of these columns are "in the
  workflow" for status purposes.
- **Tracking comment**: a Linear comment whose body begins with
  `<!-- cadence:state`, `<!-- cadence:gate`, `<!-- cadence:reconcile`, or
  the legacy `<!-- stokowski:state` / `<!-- stokowski:gate` prefixes.
- **Attempt marker**: a `cadence:state` (or legacy `stokowski:state`)
  comment whose JSON has **no** `status` field. This is what
  `/cadence:tick` step 11 counts.
- **Failure record**: a `cadence:state` comment whose JSON includes
  `"status": "failed"`. Emitted on a subagent exception. **Not** an attempt
  marker — `/cadence:tick` step 11 does not count it.

You only need one Linear MCP server connected to this session. Tool names
vary by vendor; commonly `mcp__linear__list_issues`,
`mcp__linear__get_issue`, `mcp__linear__list_comments`. Use whichever
verbs are present.

---

## Step 1 — Read config

Read `.claude/workflow.yaml`. If missing or unreadable, print a clear
error naming the path and exit.

From the parsed config, extract:

- `linear.team`, `linear.project_slug`, `linear.pickup_state` — required.
- `label.cadence_active` — required.
- `label.cadence_needs_human` — required.

Then invoke Bash:
`python ${CLAUDE_PLUGIN_ROOT}/scripts/validate_workflow.py --evidence`.

- If it exits **1**, the YAML is structurally unreadable — print the script's
  stderr verbatim and exit.
- If it exits **0 or 2**, parse the JSON on stdout. Use its `states` map,
  `workflow_linear_states`, `pickup_state`, `entry_state_name`, and
  `entry_subagent` for the rest of this report (state-to-Linear mapping and
  the per-state summary). An exit code of **2** means one or more validation
  rules failed; this reporter still proceeds — a human often runs
  `/cadence:status` *because* the workflow is misconfigured. Keep the
  `evidence` array's `FAIL` blocks for the **Config warnings** section
  (step 5).

---

## Step 2 — Build the workflow-Linear-states set and reverse lookup

Construct two structures:

1. `workflowLinearStates` — the set used to filter the query. This is the
   `workflow_linear_states` array from the validator output in step 1
   (`linear.pickup_state`, then every state's `linear_state`, then every
   gate's `approved_linear_state` and `rework_linear_state`).

2. `linearToWorkflow` — a map from each Linear column **back** to its
   role in the workflow. Each entry is one of:
   - `{ kind: "pickup", workflow_state: null }` for `linear.pickup_state`.
   - `{ kind: "state", workflow_state: "<name>" }` for an agent or
     terminal state's `linear_state`.
   - `{ kind: "gate_waiting", workflow_state: "<gate>" }` for a gate's
     `linear_state`.
   - `{ kind: "gate_approved", workflow_state: "<gate>" }` for a gate's
     `approved_linear_state`.
   - `{ kind: "gate_rework", workflow_state: "<gate>" }` for a gate's
     `rework_linear_state`.

   If two configs would produce two entries for the same Linear column
   (a uniqueness violation), keep the first and remember the conflict
   for the **Config warnings** section.

---

## Step 3 — Query workflow issues

Using the Linear MCP, query the team/project named in `linear.team` /
`linear.project_slug` for issues where Linear state ∈
`workflowLinearStates`. For each issue, capture:

- `identifier` (e.g. `ENG-123`)
- `title`
- `state.name` (current Linear column)
- `priority` (numeric; null/"No priority" treated as worst)
- `updatedAt`
- `labels` (array of label names) — used to detect `cadence_active` and
  `cadence_needs_human`
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
   array to a temporary file (you may reuse one path, overwriting it per
   issue).
2. Determine the issue's workflow-state name from its `linearToWorkflow`
   entry (step 2). For a `pickup` entry there is no workflow state — use
   `entry_state_name` from step 1. Invoke Bash:
   `python ${CLAUDE_PLUGIN_ROOT}/scripts/parse_comments.py --input <commentsFile> --target-state <workflow-state name>`
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

Print the following Markdown to the user's terminal verbatim (filling in
the bracketed parts). No Linear writes have happened — this is the
entire user-visible output.

```markdown
## Cadence status — <now in UTC ISO 8601>

Team: **<linear.team>**   Project: **<linear.project_slug>**   Pickup: **<linear.pickup_state>**

### Issues in workflow

| ID | Title | Linear column | Workflow state | Attempt | Lock | Needs human |
|----|-------|---------------|----------------|---------|------|-------------|
| <identifier> | <title truncated to ~50 chars> | <state.name> | <workflow_state from linearToWorkflow, formatted per below> | <attempt or "—"> | <"🔒" if cadence_active label present, else ""> | <"🛑" if cadence_needs_human label present, else ""> |
```

**Workflow-state column formatting** (per `linearToWorkflow` entry):

| Reverse-lookup kind | Render as                       |
|---------------------|---------------------------------|
| `pickup`            | `(pickup)`                      |
| `state`             | the workflow state name         |
| `gate_waiting`      | `<gate> (waiting)`              |
| `gate_approved`     | `<gate> (approved)`             |
| `gate_rework`       | `<gate> (rework)`               |

**Row ordering**: by Linear priority ascending, then `updatedAt`
descending (newest first within a priority). This mirrors the order in
which `/cadence:tick` would pick issues up — the top of the
table is "what fires next".

If the row set is empty, replace the table with a single italic line:
`*No issues currently in workflow states.*`

### Per-state summary

Below the table, print a per-workflow-state summary. Walk the `states:`
map in declaration order and emit one line per state:

```markdown
### Per-state counts

- **<state name>** (`<linear_state>`) — N issues   <"  🔒 N locked" if any> <"  🛑 N needs-human" if any>
- **<state name>** (`<linear_state>`) — N issues   ...
```

For **gates**, emit three lines instead of one (waiting, approved,
rework):

```markdown
- **<gate name>** (gate)
  - waiting (`<linear_state>`) — N issues
  - approved (`<linear_state>`) — N issues
  - rework (`<linear_state>`) — N issues
```

For **terminal** states, render as the single-line form. Include them
even when count is 0 — the empty terminal column tells the reader the
workflow is healthy.

Also emit a single line for `pickup_state`:

```markdown
- **(pickup)** (`<pickup_state>`) — N issues
```

### Config warnings

If any of the following hold, append a **Config warnings** section after
the summary:

- The validator (step 1) exited **2** — one or more rules failed. For each
  `FAIL` block in its `evidence` array, print the rule `title` and its
  `failure` string. This covers duplicate Linear columns, an invalid `entry`,
  dangling `next` / `on_approve` / `on_rework` targets, and missing subagent
  files — all checked deterministically by the script.
- A per-issue comment fetch was degraded or returned `parse_errors`
  (per step 4).

If none, omit the section entirely.

### Footer

End with one blank line and the literal line:

```
Read-only — no Linear writes performed.
```

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
