---
description: Cadence stale-lock sweeper — clears cadence-active labels from Linear issues whose last activity is older than limits.stale_after_minutes. Read-only against the workflow; idempotent.
disable-model-invocation: true
---

# /cadence:sweep

You are the **Cadence stale-lock sweeper**. **Run exactly once and exit.**
Do not loop. Do not pick up workflow work. Do not invoke any subagent. This
command exists to clear `cadence-active` soft locks that were stranded by a
crashed or timed-out `/cadence:tick` fire (most commonly: the
`/schedule` platform killed a fire mid-tick before it could remove its own
label).

This sweeper is a safety net for **Mode A (`/schedule`)** deployments. In
Mode B (`/loop`) the operator is present and stale locks are rare; running
the sweeper there is still safe but rarely necessary.

---

## Vocabulary

- **Stale lock**: an issue that currently has the `label.cadence_active`
  label set, **and** whose most recent activity (`updatedAt` on the Linear
  issue) is older than `limits.stale_after_minutes` minutes ago. The issue
  is presumed orphaned by a fire that never released its lock.
- **Activity window**: the threshold in minutes. Read from
  `limits.stale_after_minutes` in `.claude/workflow.yaml`; default **30**
  if the field is missing or unparseable.
- **Now**: the current UTC time at the moment the sweeper runs. Resolve
  once at the start of the fire and reuse the same value throughout (so
  every issue is judged against the same reference point).

You only need one Linear MCP server connected to this session. Tool names
vary by server vendor; commonly they look like `mcp__linear__list_issues`,
`mcp__linear__get_issue`, `mcp__linear__create_comment`,
`mcp__linear__remove_label`. Use whichever names are present in your
available tool list — the verbs below describe intent, not exact names.

---

## Step 1 — Read and validate config

Invoke Bash: `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/validate_workflow.py --evidence`.

The script reads `.claude/workflow.yaml` and emits the full parsed config
(plus per-rule evidence) as JSON on stdout. The sweeper does **not**
require a valid workflow schema — it never invokes a subagent and never
advances state, and must still be able to clear stranded locks even when
the workflow is misconfigured. So treat the validation result as
**advisory**:

- If the script exits **1** (YAML missing or unreadable), print the
  script's stderr verbatim and exit. **Do not write to Linear.**
- If the script exits **2** (validation rules failed), print a
  single-line warning
  (`Cadence sweep: workflow.yaml validation reported issues — proceeding anyway; the sweeper does not require a valid schema.`)
  and continue. Parse stdout normally.
- If the script exits **0**, parse stdout. Print nothing extra.

From the parsed JSON, read:

- `label.cadence_active` — the soft-lock label name. Required; if missing
  or empty, print an error and exit.
- `linear.team` — the team key used to scope the Linear query. Required.
- `linear.project_slug` — the project to narrow the scope to. Optional;
  omit to scan team-wide. Must match whatever `/cadence:tick` uses, so
  the sweeper sees the same issue set.
- `limits.stale_after_minutes` — the activity window. If absent, null, or
  not a positive number, use **30**.

**Do not read `.claude/workflow.yaml` directly.** The script's JSON is
the sole source of truth — re-invoking it every fire is the only way
edits to the config are guaranteed to be picked up.

## Step 2 — Resolve "now"

Compute the current UTC time as an ISO 8601 string with second precision
ending in `Z` (example: `2026-05-11T14:23:01Z`). If the model context does
not include a reliable current time, invoke Bash:

- POSIX: `date -u +%Y-%m-%dT%H:%M:%SZ`
- Windows PowerShell: `Get-Date -AsUTC -Format yyyy-MM-ddTHH:mm:ssZ`

Hold this as `now`. The cutoff and per-issue stale-minutes math is owned
by `render_sweep_report.py` in step 4 — do not derive either inline.

---

## Step 3 — Query locked issues

Using the Linear MCP, query the team named in `linear.team` (narrowed to
`linear.project_slug` when that field is present in the config) for
issues where the `label.cadence_active` label is currently set. Capture
for each:

- `identifier` (e.g. `ENG-123`)
- `title`
- `updatedAt` (UTC ISO 8601)
- `state.name` (the current Linear column — for the log line)
- Any field your MCP exposes that identifies the issue (commonly an `id`
  string used by subsequent calls)

If the result is empty, print `No cadence-active locks found.` and exit
cleanly. Otherwise write the results to a temporary JSON file under the
key `locked_issues` (one object per issue with `identifier`, `title`,
`updated_at`, `state_name`) together with `now` (step 2) and
`threshold_minutes` (= `limits.stale_after_minutes` from step 1).
Hold the path as `sweepInputPath`.

**Scope.** Only query issues in the configured `linear.team` (and
`linear.project_slug` if it is set). Do **not** scan workspace-wide —
another team may use the same label name for a different purpose.

---

## Step 4 — Classify and pre-render the report

Invoke Bash: `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/render_sweep_report.py --input "$sweepInputPath"`.

The script owns the cutoff math, the per-issue stale-minutes derivation,
the stale/fresh split, the ascending-`updated_at` ordering, and the
title-truncation in the report. Its dual-stream contract:

- **stdout** — the full Markdown report (the `## Cadence sweep — <now>`
  block, both `### Cleared` and `### Still locked` sections, with
  `(none cleared)` / `(none)` already substituted when a section is
  empty). Hold this verbatim as `sweepReport` for step 6 — do not edit.
- **stderr** — a JSON object `{"cutoff": "...", "stale": [...], "fresh":
  [...]}`. Each entry in `stale` and `fresh` carries `identifier`,
  `title`, `updated_at`, `stale_minutes`, `state_name`. Hold the parsed
  `stale` list as `staleIssues` for step 5.

If the script exits non-zero, print its stderr verbatim and exit
without touching Linear.

---

## Step 5 — Sweep stale issues

For each entry in `staleIssues` (already ordered by `updated_at`
ascending):

1. Remove the `label.cadence_active` label via Linear MCP. (If the label
   was already removed between query and write — race with a `/loop` operator
   manually clearing it — treat the removal as a no-op and proceed to step 2
   anyway.)

2. Build the sweep-comment body by invoking Bash:

   ```
   python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py \
     --kind sweep \
     --cleared-at "<now>" \
     --last-activity "<entry.updated_at>" \
     --stale-minutes <entry.stale_minutes> \
     --threshold-minutes <stale_after_minutes>
   ```

   The script's stdout is the full comment body — the HTML-comment JSON
   marker line followed by the human-readable `**[Cadence]** Stale lock
   cleared ...` line. Post it verbatim via Linear MCP. If your MCP server
   rejects the HTML-comment prefix for any reason, fall back to posting
   just the visible second line — the important side effect is the label
   removal, not the audit comment.

**Important constraints**:

- **Do not** modify the issue's Linear state. The sweeper only removes a
  label; the next `/cadence:tick` fire will pick the issue up again
  (subject to all the normal eligibility checks — attempt cap,
  `cadence-needs-human` label, etc.).
- **Do not** add `cadence-needs-human`. A stranded lock is not the same as
  a permanently failed issue; let the regular retry path decide.
- **Do not** delete or modify existing tracking comments. The sweep
  comment is added as new audit history.
- The sweep comment is **not** a tracking comment in the
  `/cadence:tick` sense — the router's attempt counter ignores it.

---

## Step 6 — Print summary

Print `sweepReport` (the stdout captured in step 4) verbatim. If any
per-issue write in step 5 failed, append one line per failure to the
end of the report (`Failed to sweep <ID>: <error>`). End with a blank
line and exit.

---

## Constraints

### Idempotency

Running the sweeper twice in quick succession is safe. The second run
sees the freshly-cleared issues without the label and skips them. The
second run's `cadence:sweep` comment will not duplicate the first.

### Side-effect ordering

- Errors **before** step 5 (config read, MCP query, classification
  render) cause a clean exit with no Linear writes.
- Step 4 pre-renders the full report on the assumption that every stale
  issue gets swept. The actual label removal + comment post happens in
  step 5; a per-issue failure does **not** reach back into the report's
  `### Cleared` table.
- Errors **during** step 5 (per-issue) should not abort the whole sweep.
  Catch the error, append a single line at the end of `sweepReport`
  (`Failed to sweep <ID>: <error>`), and continue with the next issue.
  Partial progress is better than none — the next sweep will retry the
  failed ones.

### Concurrency

- The sweeper does **not** acquire its own lock. Two sweepers running
  concurrently is harmless: both compute the same `staleIssues` list,
  both try to remove the label, the second one's removal is a no-op.
- The sweeper does **not** conflict with `/cadence:tick` running
  on the same issue: the tick holds the lock for at most one fire's
  duration; if the tick is alive, `updatedAt` will be recent and the
  sweeper will treat the issue as fresh.

### Threshold tuning guidance (for the human reader)

- Default 30 minutes is conservative for the `/schedule` platform's
  ~30-minute fire ceiling — a single hung fire will not be swept until
  it has clearly exceeded the platform's own timeout.
- If your subagents routinely take longer than 30 minutes per fire
  (rare; consider splitting work instead), raise `stale_after_minutes`
  accordingly.
- If you want faster recovery and your fires always finish in ≤5 minutes,
  setting `stale_after_minutes: 10` is reasonable.

