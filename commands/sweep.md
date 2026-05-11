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

## Step 1 — Read config

Read `.claude/workflow.yaml`. If the file is missing or unreadable, print
a clear error naming the path and exit. **Do not write to Linear.**

From the parsed config, extract:

- `label.cadence_active` — the soft-lock label name. Required; if missing,
  print an error and exit.
- `linear.team` — the team key used to scope the Linear query. Required.
- `linear.project_slug` — the project to scope to. Required.
- `limits.stale_after_minutes` — the activity window. If absent, null, or
  not a positive number, use **30**.

This sweeper does **not** validate the full workflow schema (no need —
it never invokes a subagent and never advances state). If the file
exists and the four values above are present and well-formed, proceed.

## Step 2 — Resolve "now"

Compute the current UTC time as an ISO 8601 string with second precision
ending in `Z` (example: `2026-05-11T14:23:01Z`). If the model context does
not include a reliable current time, invoke Bash:

- POSIX: `date -u +%Y-%m-%dT%H:%M:%SZ`
- Windows PowerShell: `Get-Date -AsUTC -Format yyyy-MM-ddTHH:mm:ssZ`

Hold this as `now`. Compute `cutoff = now - stale_after_minutes minutes`
(also UTC ISO 8601). Any issue with `updatedAt <= cutoff` is stale.

---

## Step 3 — Query locked issues

Using the Linear MCP, query the team/project named in `linear.team` /
`linear.project_slug` for issues where the `label.cadence_active` label
is currently set. Capture for each:

- `identifier` (e.g. `ENG-123`)
- `title`
- `updatedAt` (UTC ISO 8601)
- `state.name` (the current Linear column — for the log line)
- Any field your MCP exposes that identifies the issue (commonly an `id`
  string used by subsequent calls)

Sort by `updatedAt` ascending (oldest first). If the result is empty,
print `No cadence-active locks found.` and exit cleanly.

**Scope.** Only query issues in the configured `linear.team` /
`linear.project_slug`. Do **not** scan workspace-wide — another team may
use the same label name for a different purpose.

---

## Step 4 — Classify each locked issue

For each issue in the query result, compare its `updatedAt` to `cutoff`:

- `updatedAt > cutoff` → **fresh**. Another fire is plausibly still
  working on it; leave the lock alone.
- `updatedAt <= cutoff` → **stale**. Sweep it (step 5).

Build two lists: `staleIssues` and `freshIssues` (the latter is only used
for the summary in step 6).

---

## Step 5 — Sweep stale issues

For each issue in `staleIssues`, in `updatedAt`-ascending order:

1. Remove the `label.cadence_active` label via Linear MCP. (If the label
   was already removed between query and write — race with a `/loop` operator
   manually clearing it — treat the removal as a no-op and proceed to step 2
   anyway.)

2. Post a brief Linear comment naming the staleness, body verbatim:

   ```
   <!-- cadence:sweep {"cleared_at":"<now>","last_activity":"<updatedAt>","stale_minutes":<integer minutes between updatedAt and now>} -->
   **[Cadence]** Stale lock cleared (last activity <updatedAt>, <integer minutes> minutes ago, threshold <stale_after_minutes> minutes).
   ```

   The integer-minutes value is `floor((now - updatedAt) / 60 seconds)`.
   If your MCP server rejects the JSON HTML-comment prefix for any
   reason, fall back to posting just the human-readable line — the
   important side effect is the label removal, not the audit comment.

3. Capture `{identifier, title, updatedAt, stale_minutes}` for the
   summary in step 6.

**Important constraints**:

- **Do not** modify the issue's Linear state. The sweeper only removes a
  label; the next `/cadence:tick` fire will pick the issue up again
  (subject to all the normal eligibility checks — attempt cap,
  `cadence-needs-human` label, etc.).
- **Do not** add `cadence-needs-human`. A stranded lock is not the same as
  a permanently failed issue; let the regular retry path decide.
- **Do not** delete or modify existing tracking comments. The `cadence:sweep`
  comment is added as new audit history.
- The sweep comment is **not** a tracking comment in the
  `/cadence:tick` sense — step 11's attempt counter ignores it.

---

## Step 6 — Print summary

Print a short Markdown report to the user:

```markdown
## Cadence sweep — <now>

- Threshold: **<stale_after_minutes>** minutes (cutoff <cutoff>)
- Locked issues found: **<total>**  (stale: **<stale count>**, fresh: **<fresh count>**)

### Cleared

| Identifier | Title | Last activity | Stale (min) |
|------------|-------|---------------|-------------|
| <ID>       | <title truncated to ~60 chars> | <updatedAt> | <stale_minutes> |
| ...        | ...   | ...           | ...         |

### Still locked (fresh — below threshold)

| Identifier | Title | Last activity |
|------------|-------|---------------|
| <ID>       | <title truncated to ~60 chars> | <updatedAt> |
| ...        | ...   | ...           |
```

If `staleIssues` is empty, omit the **Cleared** table and print
`(none cleared)` in its place. Likewise for the fresh table. End the
report with a blank line and exit.

---

## Constraints

### Idempotency

Running the sweeper twice in quick succession is safe. The second run
sees the freshly-cleared issues without the label and skips them. The
second run's `cadence:sweep` comment will not duplicate the first.

### Side-effect ordering

- Errors **before** step 5 cause a clean exit with no Linear writes.
- Errors **during** step 5 (per-issue) should not abort the whole sweep.
  Catch the error, log a single line to the report (`Failed to sweep
  <ID>: <error>`), and continue with the next issue. Partial progress is
  better than none — the next sweep will retry the failed ones.

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

### Quoting

The `cadence:sweep` JSON in step 5 must be valid JSON. The `updatedAt`
value comes straight from Linear; pass it through unchanged. The
`cleared_at` value is the `now` from step 2. The `stale_minutes` value
is an integer (not a string).
