---
description: Cadence dispatch tick — runs one workflow step against the next eligible Linear issue. Reads .claude/workflow.yaml, picks an issue, invokes the matching subagent, advances Linear state. Pass "dry-run" to validate config without side effects.
argument-hint: "[dry-run]"
disable-model-invocation: true
---

# /cadence:tick

You are the dispatch tick for the **Cadence** workflow. **Run exactly once and exit.**
Do not loop. Do not start follow-up work. Do not invoke any tool not required by the
steps below. The agent that follows you on the next `/schedule` or `/loop` fire will
pick up the next issue.

Invocation arguments (verbatim, may be empty): `$ARGUMENTS`

---

## Vocabulary

- **Workflow state**: a state defined in `.claude/workflow.yaml` under `states:`
  (e.g. `plan`, `implement`, `review`, `done`). Each has a `type` of `agent`,
  `gate`, or `terminal`.
- **Linear state**: a column on the Linear board (e.g. "Planning", "In Review").
  Workflow states declare their `linear_state`. Gates declare a single
  `linear_state` — the waiting column — and signal their verdict via labels
  (see **Gate verdict labels** below), not via additional columns.
- **Workflow Linear states**: the set of every `linear_state` plus
  `linear.pickup_state`. Linear columns *outside* this set are foreign to the
  workflow — Cadence does not pick up issues sitting in them.
- **Gate verdict labels**: `label.cadence_approve` and `label.cadence_rework`.
  A human adds one to an issue sitting in a gate's waiting column to signal
  their decision. On the next fire the bootstrap reads the label, acts on it,
  and removes it. Two labels cover every gate in the workflow — the column
  identifies which gate; the label only carries the verdict.
- **Tracking comment**: a Linear comment whose body begins with one of:
  - `<!-- cadence:state {...JSON...} -->`     workflow-state attempt marker or failure record
  - `<!-- cadence:gate {...JSON...} -->`      gate transition record
  - `<!-- cadence:reconcile {...JSON...} -->` drift reconciliation note
  - The same prefixes with `stokowski:` instead of `cadence:` are accepted as
    **legacy**. When parsing legacy JSON, treat the field `run` as `attempt`
    and the field `timestamp` as `started_at`. All other semantics are identical.
- **Attempt marker**: a `cadence:state` (or legacy `stokowski:state`) tracking
  comment whose JSON has **no** `status` field. Emitted by step 12 at the start
  of every attempt. Step 11 counts these.
- **Failure record**: a `cadence:state` tracking comment whose JSON includes
  `"status": "failed"`. Emitted on subagent exception. **Not** counted as an
  attempt marker.

You only need one Linear MCP server connected to this session. Tool names vary by
server vendor; commonly they look like `mcp__linear__list_issues`, `mcp__linear__get_issue`,
`mcp__linear__create_comment`, `mcp__linear__update_issue`, `mcp__linear__list_comments`,
`mcp__linear__add_label`, `mcp__linear__remove_label`. Use whichever names are present
in your available tool list — the verbs below describe intent, not exact names.

---

## Step 0 — Dry-run branch

Trim `$ARGUMENTS` of surrounding whitespace. If the trimmed value matches `dry-run`
case-insensitively (i.e. the user typed `/cadence:tick dry-run`):

1. Run step 1 (read config) and step 2 (read global prompt) below exactly as
   written. Then invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/validate_workflow.py --evidence`
   This replaces the prose validation (step 3) and the workflow-Linear-states
   build (step 4) for the dry-run path — the script emits both the per-rule
   evidence and the `workflow_linear_states` set as JSON on stdout. Parse that
   JSON; if the script's stdout is not parseable, print stderr verbatim and
   exit.
2. Do **NOT** call any Linear MCP tool. Do **NOT** invoke any subagent. Do **NOT**
   write to any file.
3. Compose the **Lifecycle Context block** (see step 13) for a *hypothetical* issue
   sitting in the `entry` state, using these placeholder values:
   - `identifier`: `EXAMPLE-1`
   - `title`: `Hypothetical entry-state issue`
   - `url`: `https://linear.app/example/issue/EXAMPLE-1`
   - `state_name`: the `entry` state's workflow-state name (from config)
   - `attempt`: `1`
   - `priority`: `3 (Medium)`
   - `branchName`: `example/example-1-hypothetical-entry-state-issue`
   - `labels`: `(none)`
   - `description`: `No description provided.`
   - No rework section.
4. Print a single Markdown report. Start with a **Validation** section built
   from the script's JSON output. The `evidence` array has one block per rule,
   each with `rule`, `title`, `lines` (pre-formatted bullet strings),
   `result` (`PASS` / `FAIL`), and `failure`. For each block **in order**,
   print the `title`, every string in `lines` as a bullet, and the `result`.
   If a block's `result` is `FAIL`, also print its `failure` string. Do not
   re-derive any of this — the script already did the work; your job is to
   render it.

   If the script exited non-zero (`valid` is `false`): stop after printing the
   evidence for every rule up to and including the first `FAIL`, end with
   `DRY RUN — no side effects.`, and exit. Skip the rest of the report.

   If the script exited zero, follow the Validation section with:
   - **Workflow Linear states queried:** the `workflow_linear_states` array
     from the script output, one per line.
   - **Entry state:** `entry_state_name` plus `entry_subagent` from the script
     output.
   - **Lifecycle Context (composed):** the block, fenced exactly as a normal
     subagent invocation would receive it. Append `.claude/prompts/global.md`
     content (or `(empty)`) after the block.
5. End the report with the literal line: `DRY RUN — no side effects.`
6. Exit. Do not proceed to step 1 of the live path.

---

## Step 1 — Read config

Read `.claude/workflow.yaml`. If the file is missing or unreadable, print a clear
error naming the path and exit. **Do not write to Linear.**

## Step 2 — Read global prompt

Read `.claude/prompts/global.md`. If the file is missing, use the empty string.
Hold the contents in memory as `globalPrompt` for step 13.

## Step 3 — Validate config

Invoke Bash: `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/validate_workflow.py`.

This script enforces the config rules deterministically (uniqueness of every
`linear_state` value plus `linear.pickup_state`; `entry` resolves to a
`type: agent` state; every `next` / `on_approve` / `on_rework` resolves;
every `subagent` resolves to `.claude/agents/{name}.md` on disk;
`linear.pickup_state` non-empty; any `max_in_flight` value is a positive
integer (>= 1) and appears only on `type: agent` states (Rule 6); any
`adversarial_context` field is a boolean and appears only on `type: agent`
states (Rule 7); no gate state carries the legacy `approved_linear_state` /
`rework_linear_state` keys — those were removed in P4 and the validator
rejects them with a Rule 8 failure).

- If the exit code is **non-zero**, print the script's stderr verbatim and
  exit. **Do not write to Linear.** (Exit 1 means the YAML was unreadable;
  exit 2 means one or more rules failed.)
- If the exit code is **zero**, parse the JSON on stdout. Use it as the
  validated workflow config for the rest of the fire — in particular
  `workflow_linear_states`, `entry_state_name`, `entry_subagent`, and
  `pickup_state`. The raw config you read in step 1 still supplies
  `linear.team` / `linear.project_slug` / `label.*` / `limits.*`.

## Step 4 — Build the workflow Linear-states set

The validator in step 3 already produced this. Keep its `workflow_linear_states`
array in memory as `workflowLinearStates` (ordered: `linear.pickup_state`, then
every state's `linear_state`). Step 5 uses it to filter the query; step 8 uses
it to map a Linear column back to a workflow state.

---

## Step 5 — Pick work

Using the Linear MCP, query the team named in `linear.team` (narrowed to
`linear.project_slug` when that field is present) for issues where:

- Linear state ∈ `workflowLinearStates`.
- The `label.cadence_active` label is **NOT** set.
- The `label.cadence_needs_human` label is **NOT** set.
- All Linear "blocked by" relations are resolved — every blocker issue must be
  in a Linear state **outside** `workflowLinearStates` (i.e. a foreign terminal
  state like "Done" or "Cancelled", or one not modelled by this workflow). If
  blocker data is not available from the MCP query, skip this filter and proceed.

**Query shape requirements** (do not deviate):

- Pass `linear.team` to the MCP tool's team filter parameter (commonly
  named `team`) verbatim.
- If `linear.project_slug` is present in the config, pass its value to
  the MCP tool's project filter parameter (commonly named `project`)
  verbatim. Do **not** transform the value, strip suffixes, attempt to
  resolve it to a different identifier, or split it. If the consumer
  wrote a malformed value, the empty result below is the correct
  response.
- If `linear.project_slug` is **absent** (omitted, null, or empty
  string), do **not** pass any project filter — the query is team-wide
  and picks up eligible issues regardless of project assignment.
- If the query returns zero issues, that is the answer. Do **NOT** retry
  with a broader query (e.g. dropping the project filter when one was
  configured, or removing the team filter) and do **NOT** fall back to
  per-issue lookups by identifier. A misconfigured `project_slug` or
  `team` must surface as "no eligible issues" so the operator notices
  and fixes the config, rather than being papered over by an improvised
  fallback that masks the misconfiguration.

Sort the results by Linear priority ascending (lower numeric = higher priority;
treat null / "No priority" as the worst), then by `createdAt` ascending. Keep
the result as an ordered list, `candidates`.

**Apply per-state concurrency caps.** For each workflow state that has a
`max_in_flight` key (an optional positive integer; only valid on `type: agent`
states — the validator enforces this in Rule 6):

1. Query the Linear MCP for issues in `linear.team` (narrowed to
   `linear.project_slug` if present) whose current Linear column equals this
   state's `linear_state`. Count the result; call it `inFlightCount`. This
   count includes any issues with the `cadence_active` lock label — a paused
   in-flight issue still occupies a slot from a downstream coordination
   perspective.
2. If `inFlightCount >= max_in_flight`, mark this state as **over-cap** for
   this fire.

Then filter `candidates`:

- For each candidate, determine which workflow state it would target if picked
  up — in most cases this is the workflow state whose `linear_state` matches
  the candidate's current Linear column; for issues sitting in
  `linear.pickup_state` it is the `entry` state.
- If the target state is over-cap, drop the candidate from `candidates` and
  continue with the next one.

If `candidates` is empty (either no eligible issues to begin with, or every
candidate's target state is over-cap), print `No eligible issues.` and exit —
if the empty set came from cap filtering, append the line
`(caps reached for: <comma-separated over-cap state names>)` on the next
line before exiting.

---

## Step 6 — Acquire soft lock (with race retry)

Iterate `candidates` from the top. For each candidate, up to **3** total
attempts in this fire:

1. Add the `label.cadence_active` label to the issue via the Linear MCP.
2. Re-read the issue immediately (`get_issue` or equivalent). If the
   `cadence_active` label is now present **and** the issue does not have other
   markers indicating a concurrent fire claimed it (you re-read because the
   MCP add-label operation is not necessarily atomic w.r.t. another fire), this
   candidate is yours — proceed to step 7.
3. If between query (step 5) and re-read the label was already present (label
   race lost — another fire grabbed it), discard this candidate and move to
   the next one in `candidates`. This counts as one of the 3 attempts.

If you have tried 3 candidates without acquiring a lock, exit cleanly without
further side effects. The next fire will retry.

Throughout the rest of this fire, the locked issue is `issue`.

---

## Step 7 — Move issue out of pickup state (if applicable)

Read `issue`'s current Linear state. If it equals `linear.pickup_state`, move it
to the `entry` state's `linear_state` via Linear MCP. (This is the only state
transition that happens before the workflow-state determination in step 8 —
new issues enter the workflow here.)

Otherwise, leave the Linear state untouched.

---

## Step 8 — Determine the matched workflow state

Re-read `issue`'s Linear state (after the possible move in step 7). Find the
single workflow state whose `linear_state` equals it. Call this the
**matched workflow state**. By the uniqueness rule in step 3 exactly one
match is possible.

A gate now lives in exactly one column (its `linear_state`, the waiting
queue) — verdicts are signalled by labels, not by moving the card to a
different column. Step 10 handles the label branch.

If **no** state matches (the issue moved to a column outside the workflow set
between step 5 and now — possible if a human dragged it), post a plain comment:

> **[Cadence]** Issue moved to unmapped Linear state `<state>` between pickup and
> dispatch; releasing lock without action.

Remove the `cadence_active` label and exit.

---

## Step 9 — Drift check via tracking comments

Fetch the issue's full comment list via the Linear MCP. Write it verbatim as a
JSON array to a temporary file — call it `commentsFile` (use the Write tool;
any writable path works, an OS temp directory is fine). Each array element
should carry whatever `id` / `body` / `createdAt` / `user` fields the MCP
returns; `parse_comments.py` tolerates both camelCase and snake_case keys.
Keep `commentsFile` for reuse in steps 10c and 11.

Invoke Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/parse_comments.py --input <commentsFile> --target-state <matched workflow state>`
— and append `--gate-name <matched workflow state>` if the matched state from
step 8 is a gate (this makes step 9's output also carry the `rework_count` and
`rework_context` that step 10c needs, so it does not have to re-run the script).

Parse the JSON on stdout. Read `latest_tracking_comment` — its `state` field is
the workflow-state name the *last* Cadence fire was working on. It is `null`
when there is no prior `cadence:state` / `cadence:gate` / `cadence:reconcile`
comment, or when the latest such comment is a reconcile (which carries no
`state`). If `parse_errors` is non-empty, note it in your run output but
proceed.

Compare `latest_tracking_comment.state` to the matched workflow state from
step 8 (the workflow state **name**, not its `linear_state` string). Apply
these checks in order — the first one that holds wins:

- **`latest_tracking_comment.state` is `null`**: no drift (brand-new issue,
  or the latest tracking comment is a reconcile which carries no `state`).
  Proceed.

- **Match** (`latest_tracking_comment.state` equals the matched state): no
  drift. The previous fire didn't advance — either its subagent failed and
  Linear stayed where it was, or this fire is the next pickup of a gate
  still sitting in its single waiting column awaiting a verdict label.
  Proceed.

- **Normal forward progression**: the matched state equals
  `config.states[latest_tracking_comment.state].next`. This is the
  expected pattern after a successful agent→agent transition — the prior
  fire ran the subagent for state X, advanced Linear to X's successor at
  its step 16, and exited; this fire is now picking up X's successor for
  the first time. (Step 16 emits a fresh tracking comment only when
  advancing into a gate, not when advancing into another agent state — so
  for agent→agent the latest tracking comment legitimately lags one state
  behind Linear's column.) Proceed without posting a reconcile.

  This check only applies when `latest_tracking_comment.state` names an
  agent state with a defined `next` field. Gate states use `on_approve` /
  `on_rework` instead of `next`, but in practice gate predecessors are
  always handled by the **Match** rule above (Linear stays in the gate's
  waiting column until the bootstrap routes the verdict on the next fire,
  and any successful gate transition emits its own tracking comment that
  updates `latest` to the new target state).

- **Drift otherwise**: a human (or another tool) reassigned the issue to a
  column that isn't reachable from where it last was via one workflow
  edge. Build the reconcile comment by invoking Bash:
  `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind reconcile --observed-linear-state "<current Linear column>" --expected-state "<latest_tracking_comment.state>" --reason "human reassigned"`
  Post the script's stdout as a Linear comment verbatim. Then continue
  using the matched workflow state from step 8.

---

## Step 10 — Gate handling

If the matched workflow state from step 8 is **not** a gate (it's `type: agent`),
the **target state** for the rest of this fire equals the matched workflow state.
Skip to step 11.

If it **is** a gate, fetch the issue's current label list (re-read from the
Linear MCP; the lock-acquisition read in step 6 covers it if your MCP returned
labels on that response). Check for the two verdict labels
(`label.cadence_approve` and `label.cadence_rework`) and branch:

### 10a — Neither verdict label present (waiting)

The human has not decided yet. Remove the `cadence_active` label and exit.
Do **not** invoke a subagent. Do **not** post any comment. The issue stays
in the gate's waiting column until the human adds a verdict label, which
the next fire will see.

### 10b — `label.cadence_approve` is present

The human approved. Look up `<gate>.on_approve` in the config; call it
`approveTarget`.

1. Remove the `label.cadence_approve` label from the issue via Linear MCP.
2. Move the issue to `approveTarget`'s `linear_state` via Linear MCP.
3. If `approveTarget` is `type: terminal`: remove the `cadence_active` label
   and exit. No subagent invocation; the Linear state change is the audit
   record.
4. Otherwise: set the **target state** for the rest of this fire to
   `approveTarget` and continue at step 11.

### 10c — `label.cadence_rework` is present

The human is sending the work back. Look up `<gate>.on_rework` in the config;
call it `reworkTarget`. `<gate>.max_rework` may or may not be defined.

1. Remove the `label.cadence_rework` label from the issue via Linear MCP.

2. From step 9's `parse_comments.py` output (step 9 passed `--gate-name`
   because the matched state is a gate), read `rework_count` — the number of
   prior `cadence:gate` / legacy `stokowski:gate` comments with
   `"status": "rework"` whose `state` equals this gate's name. Call it
   `reworkCount`.

3. If `<gate>.max_rework` is defined and `reworkCount >= max_rework`, escalate:
   - Build the escalation comment by invoking Bash:
     `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind gate --state <gate> --status escalated`
     Post its stdout as a Linear comment verbatim.
   - Add the `label.cadence_needs_human` label.
   - Remove the `cadence_active` label and exit.

4. **Gather rework context.** From the same step 9 `parse_comments.py` output,
   read the `rework_context` array — comments posted after the most recent
   tracking comment, excluding tracking comments and obvious bots, oldest-first,
   each with `body` / `author` / `createdAt`. Keep this as `reworkComments` for
   step 13.

5. Build the rework gate comment by invoking Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind gate --state <gate> --status rework --rework-to <reworkTarget>`
   Post its stdout as a Linear comment verbatim.

6. Move the issue to `reworkTarget`'s `linear_state` via Linear MCP.

7. Set the **target state** to `reworkTarget` and continue at step 11.

### Both verdict labels present

Treat as **rework** — it is the safer verdict (routes the issue back for
another human pass rather than advancing). Remove **both** verdict labels,
then proceed exactly as 10c from step 2 onward.

A Linear label group on the two verdict labels (recommended in the docs)
makes this case structurally unreachable from the UI, but the bootstrap
still guards against it for defence in depth — a future API caller can
bypass the UI.

---

## Step 11 — Attempt cap

Let `targetState` be the target state from step 10 (or step 8 if the matched
workflow state was `type: agent`).

Determine `attemptCount` by invoking Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/parse_comments.py --input <commentsFile> --target-state <targetState>`
and reading `attempt_count` from the JSON on stdout. Re-run the script here
rather than reusing step 9's output: `targetState` may differ from the matched
workflow state — e.g. after a gate routed this fire to `reworkTarget`. The
script counts only `cadence:state` / legacy `stokowski:state` attempt markers
whose JSON `state` equals `targetState` **and** that have **no** `status` field;
failure records (`"status": "failed"`) do not count.

If `attemptCount >= limits.max_attempts_per_issue`:
1. Post a plain comment:
   > **[Cadence]** Max attempts (`<max>`) reached at state **<targetState>**.
   > Needs human intervention.
2. Add the `label.cadence_needs_human` label.
3. Remove the `cadence_active` label and exit.

Otherwise, let `attempt = attemptCount + 1` and continue.

---

## Step 12 — Emit attempt marker

Compute the current UTC timestamp as an ISO 8601 string with second precision
ending in `Z` (example: `2026-05-10T14:23:01Z`). If the model context does not
include a reliable current time, invoke Bash to run
`date -u +%Y-%m-%dT%H:%M:%SZ` (POSIX) or, on Windows PowerShell,
`Get-Date -AsUTC -Format yyyy-MM-ddTHH:mm:ssZ` and use the output.

Build the attempt-marker comment by invoking Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind state --state <targetState> --attempt <attempt> --started-at <ISO8601>`
Post its stdout as a Linear comment verbatim. The script emits no `status`
field — this comment **is** the attempt marker counted by step 11 on future
fires.

---

## Step 13 — Compose the Lifecycle Context block

The block below is the **prefix** of the subagent's user prompt. Construct it
verbatim, in this shape (the comment delimiters and headings are part of the
contract):

```
<!-- AUTO-GENERATED BY CADENCE — DO NOT EDIT -->

## Lifecycle Context

- **Issue:** {identifier} — {title}
- **URL:** {url}
- **State:** {targetState}
- **Attempt:** {attempt}
- **Priority:** {priority — render as "N (Label)" e.g. "2 (High)", or "(none)" if null}
- **Branch (Linear suggested):** {issue.branchName, else derive: "<team-key-lowercased>/<identifier-lowercased>-<title-slug>" where title-slug is the title lowercased with runs of non-alphanumerics replaced by single hyphens and trimmed to 50 chars}
- **Labels:** {comma-separated list of label names, or "(none)"}

### Description

{issue.description verbatim, or the literal "No description provided." if empty/null}

### Transitions

- On success → **{nextState}** (Linear: "{nextState's linear_state}")
- {If next state is type: gate: append a line "- Gate downstream: human will see this in Linear column \"{linear_state}\" and decide approve/rework."}
- {If next state is type: terminal: append a line "- Terminal state: the bootstrap will close the workflow at \"{linear_state}\"."}
```

If this fire entered via the **rework branch** (step 10c), append a Rework Context
section *before* the "When Done" footer. Otherwise omit it:

```
### Rework Context

This is a **rework run** at state `{targetState}`. A previous submission was
reviewed and sent back. Address the feedback below before resubmitting.

> {reworkComment[0].body}
> — {reworkComment[0].author} at {reworkComment[0].createdAt}

> {reworkComment[1].body}
> — {reworkComment[1].author} at {reworkComment[1].createdAt}

(... one block-quoted entry per reworkComment, oldest first ...)
```

If `reworkComments` is empty but this is a rework run (the gate was rework-clicked
with no accompanying human comments), include the Rework Context heading with
the body: `(No human comments were left when this issue was sent back; address
whatever you can infer from the prior review and proceed.)`.

Always finish with the footer:

```
### When Done

Do the work described by your subagent definition. When you are finished, return
a Markdown summary of:
- What you changed (files, branch, PR URL if relevant).
- What you verified (tests passed, lints clean, etc.).
- Anything the next state will need.

Do NOT post anything to Linear yourself. Do NOT modify Linear state. The
bootstrap will handle those.

<!-- END CADENCE LIFECYCLE -->
```

**Adversarial-context variant**: if the target state's config has
`adversarial_context: true`, compose the Lifecycle Context block
differently:

- The **Description** section is the ticket description verbatim — same
  as the default.
- The **Acceptance Criteria** are guaranteed to be in the description
  (P3 makes this a planner-enforced contract); the subagent reads them
  directly out of the description text.
- **No "Plan summary" or implementer-narrative section is included.**
  Even if prior tracking comments contain a plan summary or
  implementation notes, do NOT lift them into the Lifecycle Context.
- The **Branch** line is replaced with two lines:
  - **Branch (under review):** the implementer's branch name (same
    derivation as the default).
  - **Base branch:** `main` unless the repo's default is something else
    (read from `gh repo view --json defaultBranchRef -q
    .defaultBranchRef.name` if available; otherwise default to `main`).
- **PR URL**, if discoverable from `parse_comments.py`'s
  `latest_implementer_summary.pr_url` field (see step 9's parsed output),
  is included as a separate **PR:** line. If not discoverable, omit the
  line (the subagent will fall back to `git diff`).
- The **Transitions** section reads:

  ```
  ### Transitions

  - On success → **<nextState>** (Linear: "<nextState's linear_state>")
  - Your output is a Markdown findings comment. The bootstrap will post
    it on the issue and move the issue to <nextState>.
  ```

- The "When Done" footer is unchanged.

The Rework Context section, if any, is **included** for adversarial-context
subagents (a rework run still needs the human's prior rework reasoning); it
is the only narrative-style content carried into the context, and it comes
from humans, not the implementer.

If `adversarial_context` is absent or false (the default for all existing
states), compose the original Lifecycle Context block unchanged.

After the block, append two blank lines, then the contents of `globalPrompt`
(from step 2). The full string — Lifecycle Context block + blank lines + global
prompt — is the **user prompt** for the subagent invocation in step 14.

---

## Step 14 — Invoke the subagent

Look up `targetState.subagent` in the config (e.g. `planner`, `implementer`,
`reviewer`). Invoke the **Agent** tool with:

- `subagent_type`: the subagent name from config (case-sensitive, matches the
  `name` field in `.claude/agents/<subagent>.md`).
- `description`: a short string like `Cadence <targetState> for <identifier>`.
- `prompt`: the full string composed in step 13.

Run the subagent in the foreground (blocking). Capture its returned summary as
`subagentSummary`.

If the Agent tool raises an exception or the subagent returns nothing usable
(e.g. an error string framed as failure), treat this as a **failed attempt**
and skip to the **Failure path** in the Constraints section below. Do **not**
advance Linear state.

---

## Step 15 — Post the subagent's summary

Post `subagentSummary` as a Linear comment on the issue, **verbatim**. Do not
add a tracking-comment prefix; this is a plain work-product comment intended
for human readers.

If `subagentSummary` is empty or whitespace, post:
> **[Cadence]** Subagent **<subagent>** returned no summary at attempt <attempt>.

…and proceed (do **not** fail the fire — the subagent succeeded as far as we can
tell, it just produced no text).

---

## Step 16 — Advance Linear state

Look up `targetState.next` in the config. Find the next state's config block.
Then:

- If `next` is `type: agent`: move the issue's Linear state to `next.linear_state`.
- If `next` is `type: gate`: first build the gate's waiting marker by invoking
  Bash:
  `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind gate --state <next> --status waiting`
  Post its stdout as a Linear comment verbatim. Then move the issue's Linear
  state to `next.linear_state` (the gate's waiting column).

- If `next` is `type: terminal`: move the issue's Linear state to
  `next.linear_state` (e.g. "Done"). Post no further comment.

---

## Step 17 — Release the lock

Remove the `label.cadence_active` label from the issue via Linear MCP.

---

## Step 18 — Exit

Print a one-line summary to the user:
> Cadence: <identifier> advanced from **<targetState>** → **<next>** (attempt <attempt>).

Exit. Do not loop. Do not pick up another issue.

---

## Constraints

### Side-effect ordering

- **Before step 6 (lock acquisition):** any error causes a clean exit with NO
  Linear writes. Read-only operations only.
- **After step 6:** any error must, on a best-effort basis, remove the
  `cadence_active` label before exiting. If even the label-removal fails, the
  stale-lock sweeper (`/cadence:sweep`) will clear it on its next fire.
- **Never** advance Linear state after a subagent failure (see Failure path
  below). The attempt marker from step 12 stands; the failure record records
  the outcome.

### Failure path (subagent throws / returns unusable)

If the Agent invocation in step 14 raises an exception:

1. Take the subagent's exception message as the error string.
2. Build the failure record by invoking Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind state --state <targetState> --attempt <attempt> --status failed --error "<exception message>" --subagent <subagent>`
   (The script collapses newlines to spaces and truncates the error to 400
   chars itself.) Post its stdout as a Linear comment verbatim.

   This uses the same `attempt` number as the attempt marker from step 12,
   **plus** a `status: "failed"` field. It is a failure **record**, not a new
   attempt marker; step 11 on the next fire will not count it.
3. Remove the `cadence_active` label.
4. Exit. The next fire will retry — `attempt` will be the same `attemptCount + 1`
   value (the marker from step 12 is what's counted, and it remains).

   On retry, step 11's `attemptCount` is now `attempt` (the failed attempt's
   marker), so the new fire's `attempt = attempt + 1`. Eventually
   `attemptCount >= max_attempts_per_issue` and step 11 escalates.

### Concurrency

- Process **exactly one issue per fire.** Parallelism comes from multiple fires
  on overlapping cron intervals (each grabs a different issue via the soft lock).
- Never invoke more than one subagent per fire.

### Read-before-write discipline

Every Linear write (label add/remove, state move, comment) should be preceded
in this fire's logic by reading the relevant state — except step 5's initial
query and step 6's lock acquisition (which is itself a read-after-write check).

### Quoting

All JSON in tracking comments must be valid JSON. Strings must escape `"`, `\`,
and control characters. Quoted user content (rework comments, error messages)
must not break the surrounding tracking-comment JSON; if in doubt, replace
problem characters in the `error` field with spaces.

### Legacy compatibility

When parsing or counting comments, accept both `cadence:` and `stokowski:`
prefixes. When **emitting** new comments, always use `cadence:` and the field
names `attempt` and `started_at`. Never rewrite or migrate existing legacy
comments — they stand as historical record.
