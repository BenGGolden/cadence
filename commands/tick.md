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

1. Invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/validate_workflow.py --evidence`
   The `--evidence` flag is the dry-run substitute for step 1's live invocation;
   the script also covers step 4's workflow-Linear-states build. It emits the
   per-rule evidence array, the validated config blocks (`workflow_linear_states`,
   `linear_to_workflow`, `entry_state_name`, `entry_subagent`, `pickup_state`,
   `states`), and the raw `linear` / `label` / `limits` blocks as JSON on stdout.
   Parse that JSON; if the script's stdout is not parseable, print stderr
   verbatim and exit. Also write the JSON verbatim to a temporary file (call
   it `validatorOutputPath`) using the Write tool — step 0 step 3 below feeds
   it to `compose_lifecycle_context.py`.
2. Do **NOT** call any Linear MCP tool. Do **NOT** invoke any subagent. Do **NOT**
   write to any file other than the temp file from step 1.
3. Invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/compose_lifecycle_context.py --workflow-config <validatorOutputPath> --dry-run`
   The script reads the validator's `entry_state_name` and the entry state's
   `linear_state` / `next` / `adversarial_context` from `<validatorOutputPath>`,
   synthesises a hypothetical `EXAMPLE-1` issue internally, and renders the
   Lifecycle Context block plus the appended `.claude/prompts/global.md`
   content (when that file exists) on stdout. Hold the stdout as
   `dryRunComposed` for step 4.
4. Print a single Markdown report. Start with a **Validation** section built
   from the validator's JSON output. The `evidence` array has one block per rule,
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
   - **Linear column → workflow map:** one bullet per entry in
     `linear_to_workflow` from the script output, in the form
     `` `<column>` → <kind> ``, with `` (<workflow_state>, <linear_state_type>) ``
     appended when `workflow_state` is non-null.
   - **Entry state:** `entry_state_name` plus `entry_subagent` from the script
     output.
   - **Lifecycle Context (composed):** `dryRunComposed` from step 3, fenced
     exactly as a normal subagent invocation would receive it. (The script's
     output already includes the appended global prompt content when
     `.claude/prompts/global.md` exists — no additional rendering needed.)
5. End the report with the literal line: `DRY RUN — no side effects.`
6. Exit. Do not proceed to step 1 of the live path.

---

## Step 1 — Read and validate config

Invoke Bash: `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/validate_workflow.py`.

This script reads `.claude/workflow.yaml` and enforces the config rules
deterministically (uniqueness of every `linear_state` value plus
`linear.pickup_state`; `entry` resolves to a `type: agent` state; every
`next` / `on_approve` / `on_rework` resolves; every `subagent` resolves
to `.claude/agents/{name}.md` on disk; `linear.pickup_state` non-empty;
any `max_in_flight` value is a positive integer (>= 1) and appears only
on `type: agent` or `type: gate` states (Rule 6 — terminals rejected);
any `adversarial_context` field is a boolean and appears only on
`type: agent` states (Rule 7); no gate state carries the legacy
`approved_linear_state` / `rework_linear_state` keys — those were removed
in P4 and the validator rejects them with a Rule 8 failure).

- If the exit code is **non-zero**, print the script's stderr verbatim and
  exit. **Do not write to Linear.** (Exit 1 means the YAML was missing or
  unreadable; exit 2 means one or more rules failed.)
- If the exit code is **zero**, parse the JSON on stdout. This is the
  **sole source of truth** for the config in this fire — including
  `workflow_linear_states`, `linear_to_workflow`, `entry_state_name`,
  `entry_subagent`, `pickup_state`, `states`, and the raw `linear` /
  `label` / `limits` blocks. **Do not read `.claude/workflow.yaml`
  directly.** Reading the YAML yourself produces a model-cacheable
  artifact that can go stale across fires in the same conversation;
  re-invoking the script every fire is the only way edits to the config
  are guaranteed to be picked up. Also write the JSON verbatim to a
  temporary file (call it `validatorOutputPath`) using the Write tool;
  step 0 (dry-run) and step 13 invoke deterministic helpers that take it
  as input.

## Step 2 — (removed in determinism P3)

*Step 2 previously read `.claude/prompts/global.md` and held it in memory
for step 13. The `compose_lifecycle_context.py` script invoked from step
13 now does the read itself. Numbering is preserved so external
references to later steps don't shift.*

## Step 3 — (removed in determinism P2)

*Step 3 previously re-invoked the validator. Step 1 now does both the
file read and the validation in one script call. Numbering is preserved
so external references to steps 4+ don't shift.*

## Step 4 — Hold the Linear-column reverse map for step 8

The validator in step 1 emits a `linear_to_workflow` reverse map — each
Linear column name keyed to an entry of the shape
`{ "kind": "pickup" | "state" | "gate_waiting", "workflow_state": "<name>" | null, "linear_state_type": "agent" | "gate" | "terminal" | null }`.
Keep it in memory as `linearToWorkflow`; step 8 uses it.

(The `workflow_linear_states` array the validator also emits is consumed
by step 5 via `filter_candidates.py --plan`; you don't need to track it
yourself.)

---

## Step 5 — Pick work

The candidate filter, priority sort, and bounded-reachability cap walk
live in `filter_candidates.py`. The bootstrap's job here is to run the
MCP queries the script tells it to and feed the results back in.

1. **Get the query plan.** Invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/filter_candidates.py --plan --workflow-config <validatorOutputPath>`
   Parse the JSON on stdout. It has two fields: `pickup_query`
   (with `team`, `project_slug`, `workflow_linear_states`) and
   `in_flight_queries` (zero or more `{state_name, linear_state}`
   entries — one per state declaring `max_in_flight` in the config).

2. **Run the pickup query.** Using the Linear MCP, query for issues in
   `pickup_query.team` whose Linear state is in
   `pickup_query.workflow_linear_states`. If `pickup_query.project_slug`
   is non-null, also narrow to that project (commonly the MCP tool's
   `project` parameter); if it is `null`, **do not pass any project
   filter** — a team-wide query is intended in that case.

   **Query shape requirements** (do not deviate):

   - Pass `pickup_query.team` to the MCP tool's team filter parameter
     (commonly named `team`) verbatim.
   - Pass `pickup_query.project_slug` verbatim when non-null. Do **not**
     transform the value, strip suffixes, attempt to resolve it to a
     different identifier, or split it. If the consumer wrote a
     malformed value, the empty result below is the correct response.
   - If the query returns zero issues, that is the answer. Do **NOT**
     retry with a broader query (e.g. dropping the project filter when
     one was configured, or removing the team filter) and do **NOT**
     fall back to per-issue lookups by identifier. A misconfigured
     `project_slug` or `team` must surface as "no eligible issues" so
     the operator notices and fixes the config, rather than being
     papered over by an improvised fallback.

   Ask the MCP for each issue's `identifier`, `current_linear_state` (the
   Linear column name), `labels`, `priority`, `createdAt`, and — if the
   MCP exposes them — its blocker issues' Linear states (as a list of
   strings on a `blockers` field; absent if the MCP does not surface
   this, in which case the blocker filter is skipped per the script's
   contract).

3. **Run the per-state in-flight queries.** For each entry in
   `in_flight_queries`, query the Linear MCP for issues in
   `pickup_query.team` (narrowed to `pickup_query.project_slug` when
   non-null) whose current Linear column equals `entry.linear_state`.
   Count the results. Build a JSON object `inFlightCounts` mapping
   `entry.state_name` to that integer count. The count includes any
   issues with the `cadence_active` lock label and (for gates) any
   verdict-bearing issues — the script's drain exemption handles the
   gate edge case; do not pre-filter here. When `in_flight_queries` is
   empty, `inFlightCounts` is `{}`.

4. **Hand the results back to the script.** Write the pickup-query
   results to a temp file as a JSON array (call it `candidatesPath`)
   using the Write tool. Write `inFlightCounts` to a second temp file
   (call it `inFlightPath`). Invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/filter_candidates.py --workflow-config <validatorOutputPath> --candidates <candidatesPath> --in-flight <inFlightPath>`
   Parse the JSON on stdout.

5. **Act on the script's output.** If `diagnostic_message` is non-null,
   print it verbatim and exit — that is the canonical "no work to do"
   message (with or without the `(caps reached for: ...)` line). If
   `diagnostic_message` is null, set `candidates = ordered_identifiers`
   and proceed to step 6 below.

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

Re-read `issue`'s Linear state (after the possible move in step 7). Look the
column name up in `linearToWorkflow` (from step 4) and read its
`workflow_state`. Call this the **matched workflow state**. By the uniqueness
rule enforced in step 1 each column appears at most once in the map.

A gate now lives in exactly one column (its `linear_state`, the waiting
queue) — verdicts are signalled by labels, not by moving the card to a
different column. Step 10 handles the label branch.

If the column is **not** present in `linearToWorkflow` (the issue moved to a
column outside the workflow set between step 5 and now — possible if a human
dragged it), post a plain comment:

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

Parse the JSON on stdout. Also write it verbatim to a temporary file (call it
`parseCommentsOutputPath`) using the Write tool — step 13 feeds it to
`compose_lifecycle_context.py` for the `rework_context` and the
`latest_implementer_summary.pr_url` (both of which are independent of
`--target-state` and `--gate-name`, so step 11's re-invocation does not
need to overwrite this file).

Read `latest_tracking_comment` — its `state` field is
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

Write the locked `issue` MCP object verbatim as JSON to a temporary file
(call it `issueJsonPath`) using the Write tool. Then invoke Bash:

`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/compose_lifecycle_context.py --workflow-config <validatorOutputPath> --issue <issueJsonPath> --target-state <targetState> --attempt <attempt> --parse-comments-output <parseCommentsOutputPath>`

If this fire entered via the **rework branch** (step 10c), append `--rework`
so the script includes the Rework Context section.

The script reads:
- The validator output (`validatorOutputPath`, from step 1) — `states[<targetState>]`
  for `adversarial_context` / `linear_state` / `next`, the resolved next
  state for its `type` / `linear_state`, and `linear.team` for branch
  derivation.
- The issue object (`issueJsonPath`, written immediately above) —
  `identifier` / `title` / `url` / `branchName` / `priority` / `labels` /
  `description`.
- The parse-comments output (`parseCommentsOutputPath`, from step 9) —
  `rework_context` (the human comments rendered when `--rework` is
  passed) and `latest_implementer_summary.pr_url` (rendered as the
  **PR:** line in the adversarial-context variant).
- `.claude/prompts/global.md` if it exists — appended verbatim after two
  blank lines.

The script handles both the default and `adversarial_context: true`
variants of the Lifecycle Context block (the latter strips
implementer-narrative content and adds **Branch (under review)** / **Base
branch** / optional **PR:** lines), the rework-section rendering
(including the zero-comments fallback), and the global-prompt append.

**Stdout is the full subagent user prompt.** Pass it as the Agent tool's
`prompt` parameter in step 14.

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

### Bootstrap silence

Between step 14 (subagent invocation) and step 18 (exit summary), the bootstrap's
only user-facing output is the `subagentSummary` posted verbatim in this step
and the per-step Linear writes that steps 16 and 17 require. **Do not annotate
the subagent's behaviour, do not describe what the subagent did during its turn,
and do not raise security or safety concerns about the subagent's activity in
user-facing text.** The bootstrap does not have access to the subagent's tool
trace; any such narration is necessarily fabricated.

If the subagent's `subagentSummary` itself flags a concern, the operator will
see it in the verbatim post — the bootstrap's job is to relay, not to interpret.
Suspicions about subagent behaviour belong in operator-facing channels (issue
the operator, file a bug against the subagent template), not in the per-fire
output.

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
