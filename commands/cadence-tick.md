---
description: Cadence dispatch tick — runs one workflow step against the next eligible Linear issue. Reads .claude/workflow.yaml, picks an issue, invokes the matching subagent, advances Linear state. Pass "dry-run" to validate config without side effects.
argument-hint: "[dry-run]"
disable-model-invocation: true
---

# /cadence:cadence-tick

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
- **Linear state**: a column on the Linear board (e.g. "Planning", "In Review",
  "Approved"). Workflow states declare their `linear_state`. Gates additionally
  declare `approved_linear_state` and `rework_linear_state`.
- **Workflow Linear states**: the set of every `linear_state`, every gate's
  `approved_linear_state`, every gate's `rework_linear_state`, plus
  `linear.pickup_state`. Linear columns *outside* this set are foreign to the
  workflow — Cadence does not pick up issues sitting in them.
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
case-insensitively (i.e. the user typed `/cadence:cadence-tick dry-run`):

1. Run steps 1–4 below exactly as written (read config, read global prompt,
   validate, build the workflow-Linear-states set).
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
4. Print a single Markdown report containing:
   - **Validation:** `passed` (or the specific error and stop).
   - **Workflow Linear states queried:** the full set from step 4, one per line.
   - **Entry state:** the workflow state name plus the subagent that would be
     invoked.
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

Validate the YAML and check every rule. On the **first** failure, print a clear
message naming the offending keys and exit (no Linear writes):

1. **Uniqueness.** Collect every `linear_state` value across `states:`, plus every
   gate's `approved_linear_state` and `rework_linear_state`. They must all be
   distinct, and none may equal any other's value. If two states share a Linear
   column, report both names and the duplicated string.
2. **Entry.** `entry` must be a string naming a defined state in `states:`. That
   state must have `type: agent`.
3. **Targets.** For each state of `type: agent`, `next` must reference a defined
   state. For each `type: gate`, `on_approve` and `on_rework` must each reference
   a defined state.
4. **Subagent files.** For each `type: agent` state, `subagent` must name a file
   at `.claude/agents/{subagent}.md` that exists. If missing, name both the state
   and the expected file path in the error.
5. **Pickup state.** `linear.pickup_state` must be a non-empty string. It need not
   appear in `states:` (it's the inbox column).

## Step 4 — Build the workflow Linear-states set

Construct an ordered set containing:
- `linear.pickup_state`
- Every state's `linear_state`
- Every gate's `approved_linear_state` and `rework_linear_state`

Keep this set in memory as `workflowLinearStates`. Step 5 uses it to filter the
query; step 8 uses it to map a Linear column back to a workflow state.

---

## Step 5 — Pick work

Using the Linear MCP, query the team/project named in `linear.team` /
`linear.project_slug` for issues where:

- Linear state ∈ `workflowLinearStates`.
- The `label.cadence_active` label is **NOT** set.
- The `label.cadence_needs_human` label is **NOT** set.
- All Linear "blocked by" relations are resolved — every blocker issue must be
  in a Linear state **outside** `workflowLinearStates` (i.e. a foreign terminal
  state like "Done" or "Cancelled", or one not modelled by this workflow). If
  blocker data is not available from the MCP query, skip this filter and proceed.

Sort the results by Linear priority ascending (lower numeric = higher priority;
treat null / "No priority" as the worst), then by `createdAt` ascending. Keep
the result as an ordered list, `candidates`.

If `candidates` is empty, print `No eligible issues.` and exit.

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
single workflow state whose:
- `linear_state` equals it, **OR**
- (for a gate) `approved_linear_state` equals it, **OR**
- (for a gate) `rework_linear_state` equals it.

Call this the **matched workflow state**. By the uniqueness rule in step 3
exactly one match is possible.

If **no** state matches (the issue moved to a column outside the workflow set
between step 5 and now — possible if a human dragged it), post a plain comment:

> **[Cadence]** Issue moved to unmapped Linear state `<state>` between pickup and
> dispatch; releasing lock without action.

Remove the `cadence_active` label and exit.

---

## Step 9 — Drift check via tracking comments

Fetch the issue's comments (newest-first or all, as the MCP supports). Find the
**latest** tracking comment — i.e. the most recent comment whose body begins with
`<!-- cadence:state` or `<!-- cadence:gate` (also accepting the legacy `stokowski:`
prefixes; see Vocabulary).

Parse its JSON. Note the `state` field — this is the workflow-state name the
*last* Cadence fire was working on.

Compare it to the matched workflow state from step 8 (the workflow state **name**,
not its `linear_state` string):

- **Match**: no drift. Proceed.
- **Mismatch**: drift. A human (or another tool) reassigned the issue. Post a
  reconcile comment:

  ```
  <!-- cadence:reconcile {"observed_linear_state":"<current Linear column>","expected_state":"<state name from last tracking comment>","reason":"human reassigned"} -->
  **[Cadence]** Detected human-driven state change; proceeding from Linear's state.
  ```

  Then continue using the matched workflow state from step 8.

- **Special case — gate sitting in approved/rework**: if the matched workflow state
  is a gate and Linear's current column is that gate's `approved_linear_state` or
  `rework_linear_state`, the matched workflow state name is **still** the gate's
  own name. If the last tracking comment also names the gate (e.g. a prior
  `cadence:gate {"state":"<gate>","status":"waiting"}`), this is **not** drift.

- **No tracking comment yet** (brand-new issue, first fire): no drift. Proceed.

---

## Step 10 — Gate handling

If the matched workflow state from step 8 is **not** a gate (it's `type: agent`),
the **target state** for the rest of this fire equals the matched workflow state.
Skip to step 11.

If it **is** a gate, branch on which of the gate's three Linear columns the issue
is in:

### 10a — Gate column is `linear_state` (waiting)

The human has not made a decision yet. Remove the `cadence_active` label and exit.
Do **not** invoke a subagent. Do not post any comment.

### 10b — Gate column is `approved_linear_state`

The human approved. Look up `<gate>.on_approve` in the config; call it
`approveTarget`.

1. Move the issue to `approveTarget`'s `linear_state`.
2. If `approveTarget` is `type: terminal`: remove the `cadence_active` label and
   exit. No subagent invocation; the Linear state change is the audit record.
3. Otherwise: set the **target state** for the rest of this fire to
   `approveTarget` and continue at step 11.

### 10c — Gate column is `rework_linear_state`

The human is sending the work back. Look up `<gate>.on_rework` in the config; call
it `reworkTarget`. `<gate>.max_rework` may or may not be defined.

1. Count prior `cadence:gate` (or legacy `stokowski:gate`) tracking comments on
   this issue whose JSON has `"status": "rework"` **and** whose `state` equals the
   current gate's name. Call this `reworkCount`.

2. If `<gate>.max_rework` is defined and `reworkCount >= max_rework`, escalate:
   - Post: `<!-- cadence:gate {"state":"<gate>","status":"escalated"} -->`
     followed by `**[Cadence]** Rework limit reached at gate **<gate>**. Needs human intervention.`
   - Add the `label.cadence_needs_human` label.
   - Remove the `cadence_active` label and exit.

3. **Gather rework context before posting the new gate comment.** Collect every
   Linear comment that:
   - Was posted *after* the most recent tracking comment (cadence:state /
     cadence:gate / cadence:reconcile, including legacy stokowski:); and
   - Is not itself a tracking comment; and
   - Was authored by a human (not by the Linear MCP integration account / not
     a bot / not the cadence-active bot — best effort, lean on author identity
     fields the MCP exposes; if uncertain, include it and let the implementer
     judge).

   Keep these as `reworkComments` — each with body + createdAt + author display
   name — for step 13.

4. Post a new gate comment:
   ```
   <!-- cadence:gate {"state":"<gate>","status":"rework","rework_to":"<reworkTarget>"} -->
   **[Cadence]** Rework requested; routing to **<reworkTarget>** (attempt counts toward <reworkTarget>'s max_attempts).
   ```

5. Move the issue to `reworkTarget`'s `linear_state` via Linear MCP.

6. Set the **target state** to `reworkTarget` and continue at step 11.

---

## Step 11 — Attempt cap

Let `targetState` be the target state from step 10 (or step 8 if the matched
workflow state was `type: agent`).

Count the **attempt markers** for `targetState` on this issue: every `cadence:state`
(or legacy `stokowski:state`) tracking comment whose JSON `state` equals
`targetState` **and** whose JSON has **no** `status` field. Comments with
`"status": "failed"` (failure records) do **not** count here. Call this
`attemptCount`.

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

Post a Linear comment with body:

```
<!-- cadence:state {"state":"<targetState>","attempt":<attempt>,"started_at":"<ISO8601>"} -->
**[Cadence]** Entering state: **<targetState>** (attempt <attempt>)
```

(Omit any `status` field — this comment **is** the attempt marker counted by
step 11 on future fires.)

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
- If `next` is `type: gate`: first post the gate's waiting marker:

  ```
  <!-- cadence:gate {"state":"<next>","status":"waiting"} -->
  **[Cadence]** Awaiting human review at **<next>**.
  ```

  Then move the issue's Linear state to `next.linear_state` (the gate's waiting
  column).

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
  stale-lock sweeper (`/cadence:cadence-sweep`) will clear it on its next fire.
- **Never** advance Linear state after a subagent failure (see Failure path
  below). The attempt marker from step 12 stands; the failure record records
  the outcome.

### Failure path (subagent throws / returns unusable)

If the Agent invocation in step 14 raises an exception:

1. Compose a short error string (the exception message, truncated to ~400 chars,
   with newlines collapsed to spaces).
2. Post a Linear comment with body:

   ```
   <!-- cadence:state {"state":"<targetState>","attempt":<attempt>,"status":"failed","error":"<error string>"} -->
   **[Cadence]** Subagent **<subagent>** failed at attempt <attempt>: <error string>
   ```

   (Same `attempt` number as the attempt marker from step 12, **plus** a
   `status: "failed"` field. This is a failure **record**, not a new attempt
   marker; step 11 on the next fire will not count it.)
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
