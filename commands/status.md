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
  `linear.pickup_state`. The same set `/cadence:tick` step 4 builds.
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
  `/cadence:tick` step 11 counts.
- **Failure record**: a `cadence:state` comment whose JSON includes
  `"status": "failed"`. Emitted on a subagent exception. **Not** an attempt
  marker — `/cadence:tick` step 11 does not count it.

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
   array to a temporary file (you may reuse one path, overwriting it per
   issue).
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

Print the following Markdown to the user's terminal verbatim (filling in
the bracketed parts). No Linear writes have happened — this is the
entire user-visible output.

```markdown
## Cadence status — <now in UTC ISO 8601>

Team: **<linear.team>**   Project: **<linear.project_slug, or "(any)" if unset>**   Pickup: **<linear.pickup_state>**

### Issues in workflow

| ID | Title | Linear column | Workflow state | Attempt | Lock | Needs human | Verdict |
|----|-------|---------------|----------------|---------|------|-------------|---------|
| <identifier> | <title truncated to ~50 chars> | <state.name> | <workflow_state from linearToWorkflow, formatted per below> | <attempt or "—"> | <"🔒" if cadence_active label present, else ""> | <"🛑" if cadence_needs_human label present, else ""> | <verdict cell per below> |
```

**Workflow-state column formatting** (per `linearToWorkflow` entry):

| Reverse-lookup kind | Render as                       |
|---------------------|---------------------------------|
| `pickup`            | `(pickup)`                      |
| `state`             | the workflow state name         |
| `gate_waiting`      | `<gate> (waiting)`              |

**Verdict column** (only meaningful for `gate_waiting` rows; otherwise
leave empty):

- `cadence-approve` if the `label.cadence_approve` label is present and
  `label.cadence_rework` is not — the next fire will route this issue
  to the gate's `on_approve` target.
- `cadence-rework` if the `label.cadence_rework` label is present and
  `label.cadence_approve` is not — the next fire will route to
  `on_rework`.
- `both (→ rework)` if both labels are present — the next fire's
  defensive guard treats this as rework.
- empty if neither is present — the gate is still waiting for a human.

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

For **gates**, render the single waiting column plus a verdict-label
breakdown so the human can see what is queued up for the next fire:

```markdown
- **<gate name>** (gate, `<linear_state>`) — N issues
  - awaiting verdict — N issues
  - 👍 cadence-approve — N issues
  - 👎 cadence-rework — N issues
  - ⚠️ both labels (treated as rework) — N issues
```

Omit any breakdown line whose count is 0. If all N issues are in the
"awaiting verdict" bucket, collapse to the single-line form.

For **terminal** states, render as the single-line form. Include them
even when count is 0 — the empty terminal column tells the reader the
workflow is healthy.

Also emit a single line for `pickup_state`:

```markdown
- **(pickup)** (`<pickup_state>`) — N issues
```

### Concurrency

If any workflow state declares `max_in_flight` (agent or gate; P6 + P8),
append a Concurrency table after the per-state summary so the human can
see whether pickup is currently throttled by caps. Walk every state in
the `states:` map (in declaration order); compute `inFlight` as the
number of issues from step 3 whose Linear column equals the state's
`linear_state` (the same counts the per-state summary already has).

```markdown
### Concurrency

| State                   | In flight | Cap    | Status   |
|-------------------------|-----------|--------|----------|
| plan                    | 1         | (none) |          |
| plan_review (gate)      | 4         | 5      |          |
| implement               | 2         | 3      |          |
| agent_review            | 1         | (none) |          |
| human_review (gate)     | 5         | 5      | AT CAP   |
| done                    | 12        | n/a    |          |
```

Cells:

- **State** — workflow-state name, suffixed with `(gate)` for gates and
  `(terminal)` for terminals. The `(pickup)` row is omitted; pickup is
  not a workflow state.
- **In flight** — `inFlight` for this state.
- **Cap** — the state's `max_in_flight` if set; `(none)` for agent or
  gate states without a cap; `n/a` for terminals (Rule 6 forbids caps
  on terminal states).
- **Status** —
  - empty when `Cap` is `(none)` or `n/a`, or when `inFlight < cap`;
  - `AT CAP` when `inFlight == cap` (the next fire's reachability walk
    will drop candidates whose happy-path downstream passes through
    this state — except verdict-bearing issues already in a capped
    gate's column, which drain regardless);
  - `OVER CAP` when `inFlight > cap` (a human moved issues in manually;
    the next fire will block upstream candidates until the count
    drops to the cap or below).

Omit the entire Concurrency section if no state declares `max_in_flight`
— a workflow without caps does not need the noise.

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
