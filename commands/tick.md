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
  comment whose JSON has **no** `status` field. Emitted by step 7 at the start
  of every attempt. The Route step (step 6) counts these.
- **Failure record**: a `cadence:state` tracking comment whose JSON includes
  `"status": "failed"`. Emitted on subagent exception. **Not** counted as an
  attempt marker.

You only need one Linear MCP server connected to this session. Tool names vary by
server vendor; commonly they look like `mcp__linear__list_issues`, `mcp__linear__get_issue`,
`mcp__linear__create_comment`, `mcp__linear__update_issue`, `mcp__linear__list_comments`,
`mcp__linear__add_label`, `mcp__linear__remove_label`. Use whichever names are present
in your available tool list — the verbs below describe intent, not exact names.

**GitHub pull-request operations** (PR create on the implement step, PR
read + merge in the `merge_on_approve` sub-phase) run via the **GitHub MCP**
tools, never the `gh` CLI. The bootstrap owns every PR operation; subagents
only `git push`. The GitHub connector is bound to the routine (or to your
local Claude Code under `/loop`), so its tools — commonly
`mcp__github__create_pull_request`, `mcp__github__list_pull_requests`,
`mcp__github__get_pull_request`, `mcp__github__merge_pull_request` — are
present and auto-authorized. Use whichever names your connector exposes. They
scope to the bound repo on their own: create needs **no** repo argument, and
read/merge take `owner` / `repo` / `pullNumber` parsed from the PR URL. No
`GH_TOKEN`, `GH_REPO`, or setup script is involved.

### Scratch files

Every transient JSON file this fire writes with the Write tool (the comment
list, the candidate/in-flight lists, the composed issue object) goes under
**`.cadence/`** — Cadence's per-repo scratch directory. Step 1 (and step 0's
`--evidence` call) runs `validate_workflow.py`, which creates `.cadence/` and a
self-ignoring `.cadence/.gitignore` (`*`) before you write anything, so these
files never show up in the consumer's `git status`. Use the stable names given
below (`.cadence/comments.json`, etc.); reusing a name across fires is fine —
each is overwritten and read back within the same fire. **Do not** write
scratch to the repo root, `tmp/`, or an OS temp directory.

---

## Step 0 — Dry-run branch

Trim `$ARGUMENTS` of surrounding whitespace. If the trimmed value matches `dry-run`
case-insensitively (i.e. the user typed `/cadence:tick dry-run`):

1. Invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/validate_workflow.py --evidence`
   The `--evidence` flag is the dry-run substitute for step 1's live invocation;
   the script also covers step 2's workflow-Linear-states build. It emits the
   per-rule evidence array, the validated config blocks (`workflow_linear_states`,
   `linear_to_workflow`, `entry_state_name`, `entry_subagent`, `pickup_state`,
   `states`), and the raw `linear` / `label` / `limits` blocks as JSON on stdout.
   Parse that JSON; if the script's stdout is not parseable, print stderr
   verbatim and exit.
2. Do **NOT** call any Linear MCP tool. Do **NOT** invoke any subagent. Do **NOT**
   write to any file.
3. Invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/compose_lifecycle_context.py --workflow-path "${CLAUDE_PROJECT_DIR:-.}/.claude/workflow.yaml" --dry-run`
   The script validates `.claude/workflow.yaml` internally to derive the
   `entry_state_name` and the entry state's `linear_state` / `next` /
   `adversarial_context`, synthesises a hypothetical `EXAMPLE-1` issue internally, and renders the
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
  are guaranteed to be picked up. The deterministic helpers
  (`route_fire.py`, `filter_candidates.py`, `compose_lifecycle_context.py`)
  re-validate `.claude/workflow.yaml` themselves via `--workflow-path` rather
  than being threaded this JSON, so the validator runs once here for the
  bootstrap's own lookups and again inside each helper — cheap, and it avoids
  any stale shared copy.

## Step 2 — The validator's derived maps feed downstream scripts

The validator in step 1 emits a `linear_to_workflow` reverse map — each
Linear column name keyed to an entry of the shape
`{ "kind": "pickup" | "state" | "gate_waiting", "workflow_state": "<name>" | null, "linear_state_type": "agent" | "gate" | "terminal" | null }`
— and a `workflow_linear_states` array. You do **not** need to hold either
in memory: the deterministic helpers that consume them re-derive both by
re-validating `.claude/workflow.yaml` internally (passed `--workflow-path`) —
the reverse map by the Route step's `route_fire.py` (matched-state lookup, old
step 8) and the states array by step 3's `filter_candidates.py --plan`.
Re-deriving them in prose would just risk drift from the script's view.

---

## Step 3 — Pick work

The candidate filter, priority sort, and bounded-reachability cap walk
live in `filter_candidates.py`. The bootstrap's job here is to run the
MCP queries the script tells it to and feed the results back in.

1. **Get the query plan.** Invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/filter_candidates.py --plan --workflow-path "${CLAUDE_PROJECT_DIR:-.}/.claude/workflow.yaml"`
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
   results as a JSON array to `.cadence/candidates.json` (call it
   `candidatesPath`) using the Write tool. Write `inFlightCounts` to
   `.cadence/in-flight.json` (call it `inFlightPath`). Invoke Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/filter_candidates.py --workflow-path "${CLAUDE_PROJECT_DIR:-.}/.claude/workflow.yaml" --candidates <candidatesPath> --in-flight <inFlightPath>`
   Parse the JSON on stdout.

5. **Act on the script's output.** If `diagnostic_message` is non-null,
   print it verbatim and exit — that is the canonical "no work to do"
   message (with or without the `(caps reached for: ...)` line). If
   `diagnostic_message` is null, set `candidates = ordered_identifiers`
   and proceed to step 4 below.

---

## Step 4 — Acquire soft lock (with race retry)

Iterate `candidates` from the top. For each candidate, up to **3** total
attempts in this fire:

1. Add the `label.cadence_active` label to the issue via the Linear MCP.
2. Re-read the issue immediately (`get_issue` or equivalent). If the
   `cadence_active` label is now present **and** the issue does not have other
   markers indicating a concurrent fire claimed it (you re-read because the
   MCP add-label operation is not necessarily atomic w.r.t. another fire), this
   candidate is yours — proceed to step 5.
3. If between query (step 3) and re-read the label was already present (label
   race lost — another fire grabbed it), discard this candidate and move to
   the next one in `candidates`. This counts as one of the 3 attempts.

If you have tried 3 candidates without acquiring a lock, exit cleanly without
further side effects. The next fire will retry.

Throughout the rest of this fire, the locked issue is `issue`.

---

## Step 5 — Move issue out of pickup state (if applicable)

Read `issue`'s current Linear state. If it equals `linear.pickup_state`, move it
to the `entry` state's `linear_state` via Linear MCP. (This is the only state
transition that happens before the workflow-state determination in the Route
step (step 6) — new issues enter the workflow here.)

Otherwise, leave the Linear state untouched.

---

## Step 6 — Route the fire (Gather → Route → Execute)

Step 6 is a single cohesive decision — *"given where this issue sits and
its history, what should this fire do to it?"* — computed deterministically by
`route_fire.py`. It subsumes the four decisions the prose used to spell out:
the matched-state lookup and unmapped-column release (old step 8), the drift
check (old step 9), the gate verdict routing (old step 10: waiting / approve /
rework, both-labels→rework, `max_rework` escalation), and the attempt-cap
check against the **resolved** target (old step 11). The bootstrap gathers the
inputs, runs the router **once**, and executes the plan it returns. The
bootstrap remains the sole Linear writer; the router only decides.

### Gather (MCP reads)

1. Re-read `issue`'s current Linear column (after the possible step-5 move).
   Call it `<column>`.
2. Re-read `issue`'s current label list (the step-6 lock re-read covers this
   if your MCP returned labels). Reduce it to the present label **names**.
3. Fetch the issue's full comment list via the Linear MCP. Write it verbatim
   as a JSON array to `.cadence/comments.json` — call it `commentsFile`
   (Write tool). Each element carries whatever `id` / `body` / `createdAt` /
   `user` fields the MCP returns; the router tolerates both camelCase and
   snake_case keys.

### Route (one Bash call)

Invoke Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/route_fire.py --workflow-path "${CLAUDE_PROJECT_DIR:-.}/.claude/workflow.yaml" --linear-state "<column>" --comments <commentsFile> --labels "<comma-separated present label names>"`

(`--labels` also accepts a path to a JSON file of label names/objects; the CSV
form is fine for the handful of labels an issue carries. Pass an empty string
when the issue has no labels.)

Parse the plan JSON on stdout. If stdout is not parseable, print stderr
verbatim and exit. The plan carries:

- `matched_state` — the workflow state `<column>` maps to (or `null`).
- `target_state` — the resolved state this fire works on (or `null` on exits).
- `attempt` — the attempt number for `target_state` (or `null` on exits).
- `rework` — `true` when this fire entered via a gate **rework** route
  (step 8 passes `--rework` when set).
- `promote_ac` — `true` when this fire entered via a gate **approve** and
  should attempt to promote planner-proposed acceptance criteria into the
  issue description (see Execute below). `false` for normal agent fires,
  rework fires, and all exits.
- `pre_actions` — ordered actions to apply before invoking the subagent.
- `invoke_subagent` — `true` to proceed to step 7; `false` to take the exit.
- `subagent` — the subagent name (when `invoke_subagent` is `true`).
- `parse_comments_output` — the full `parse_comments.py` result the router
  computed (the router parsed exactly once). Step 8 needs it; see Execute.
- `exit_plan` — ordered actions for an early-exit fire (when not invoking).
- `exit_summary` — the one-line message to print on an early-exit fire.
- `merge_on_approve` — `true` only on a terminal gate-approve fire whose gate
  declared `merge_on_approve: true`. Signals the Execute exit branch to run the
  **Merge on approve** sub-phase (below) before exiting. `false` on every other
  fire.
- `pr_url` — the PR URL extracted from the latest implementer summary (or
  `null` when none was found). Only meaningful when `merge_on_approve` is `true`.
- `merge_method` — the merge method to pass to GitHub MCP's
  `merge_pull_request` (one of `merge` / `squash` / `rebase`; defaults to
  `squash` when the gate did not configure `merge_method`). Only meaningful when
  `merge_on_approve` is `true`.
- `merge_target_linear_state` — the terminal's Linear column to move the issue
  to **after** a successful (or already-merged) merge. Only meaningful when
  `merge_on_approve` is `true`.

Each action in `pre_actions` / `exit_plan` is one of:
- `{"type": "post_comment", "body": "<body>"}` — post the body as a Linear
  comment, **verbatim**. The router already built any tracking-comment JSON
  (reconcile / gate rework / gate escalation); do not add or alter a prefix.
- `{"type": "remove_label", "label": "<name>"}` — remove the named label.
- `{"type": "add_label", "label": "<name>"}` — add the named label.
- `{"type": "move_state", "linear_state": "<column>"}` — move the issue to
  that Linear column.

The router has already done the matched-state lookup, the unmapped-column
release decision, drift detection (and its reconcile comment body), the gate
verdict routing, terminal detection, the `max_rework` escalation, and the
attempt-cap check against the **resolved** target. **Do not re-derive any of
it** — the bootstrap's job is to apply the plan.

### Execute (MCP writes)

- If `invoke_subagent` is **false**: apply every action in `exit_plan` in
  order via the Linear MCP. **Then**, if `plan.merge_on_approve` is `true`, run
  the **Merge on approve** sub-phase below (it owns the conditional move to the
  terminal and the lock release for this fire). Otherwise print `exit_summary`
  to the user and exit. This is the single path for the unmapped-column,
  gate-waiting, approve→terminal, rework-escalation, and attempt-cap fires. Do
  **not** invoke a subagent and do **not** advance further.

  **Merge on approve (gate-approve → terminal, opt-in).** Runs only when
  `plan.merge_on_approve` is `true`. The `exit_plan` actions above have already
  removed the `cadence_approve` label (and posted any drift-reconcile comment);
  this sub-phase performs the conditional terminal move and lock release. All
  Linear writes remain the bootstrap's; the GitHub PR reads/merge are the
  bootstrap's too, run via the **GitHub MCP** tools (commonly
  `mcp__github__get_pull_request` / `mcp__github__merge_pull_request`; use
  whichever names are present — they are auto-authorized in this session). The
  `owner` / `repo` / `pullNumber` arguments come from parsing `plan.pr_url`
  (`https://github.com/<owner>/<repo>/pull/<n>`). Read-before-write per Cadence
  discipline — read PR state first, then act. In every branch below, the gate
  name passed to `--state` is `plan.matched_state`.

  1. **No PR URL.** If `plan.pr_url` is null: build the audit comment via Bash
     `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind merge --status no_pr --state <plan.matched_state>`
     and post its stdout as a Linear comment verbatim; add the
     `label.cadence_needs_human` label; remove the `label.cadence_active`
     label; print `exit_summary` (noting the no-PR escalation); **exit**. The
     card stays in the gate's waiting column — it is **not** advanced to the
     terminal.
  2. **Read PR state.** Otherwise, parse `owner` / `repo` / `pullNumber` from
     `plan.pr_url` and call GitHub MCP `get_pull_request`. It returns the REST
     shape: a `state` field (`open` / `closed`) plus a `merged` boolean. If the
     call errors or returns no usable `{state, merged}`, go straight to the
     **escalate** branch (step 5) using the error text (or
     "could not read PR state") as `--error`.
  3. **`merged` is `true` (already merged — e.g. a human merged manually).**
     Build the audit comment via
     `emit_tracking_comment.py --kind merge --status already_merged --pr-url <plan.pr_url> --state <plan.matched_state>`
     and post it verbatim; move the issue to `plan.merge_target_linear_state`
     via the Linear MCP; remove the `label.cadence_active` label; print a
     summary; **exit**.
  4. **`merged` is `false` and `state` is `open` (mergeable).** Call GitHub MCP
     `merge_pull_request` with the URL-derived `owner` / `repo` / `pullNumber`
     and the merge method from `plan.merge_method` (the tool's
     `merge_method` / `mergeMethod` parameter — one of `merge` / `squash` /
     `rebase`).
     - **Success:** post
       `emit_tracking_comment.py --kind merge --status merged --pr-url <plan.pr_url> --state <plan.matched_state>`
       verbatim; move the issue to `plan.merge_target_linear_state`; remove the
       `label.cadence_active` label; print a summary; **exit**.
     - **Failure** (CI red, conflicts, branch protection — the
       `merge_pull_request` call errors or returns a non-merged result): post
       `emit_tracking_comment.py --kind merge --status failed --error "<merge error>" --state <plan.matched_state>`
       verbatim; add the `label.cadence_needs_human` label; remove the
       `label.cadence_active` label; **do not** move the issue to the terminal;
       print a summary noting the escalation; **exit**. The card stays in the
       gate's waiting column.
  5. **Escalate** (PR `closed` but not `merged` — an abandoned PR — or the
     `get_pull_request` read in step 2 failed): post
     `emit_tracking_comment.py --kind merge --status failed --error "<reason or PR-read error>" --state <plan.matched_state>`
     verbatim; add the `label.cadence_needs_human` label; remove the
     `label.cadence_active` label; leave the issue in the gate's waiting
     column; print a summary noting the escalation; **exit**.

- If `invoke_subagent` is **true**: apply every action in `pre_actions` in
  order via the Linear MCP. Then write `parse_comments_output` verbatim as
  JSON to `.cadence/parse-comments.json` (Write tool) — call it
  `parseCommentsOutputPath`; step 8 feeds it to
  `compose_lifecycle_context.py`. Then run the **Promote proposed acceptance
  criteria** sub-phase below. Carry `target_state`, `attempt`, and `rework`
  forward and continue at step 7.

  **Promote proposed acceptance criteria (gate-approve only).** If
  `plan.promote_ac` is `true`:

  1. Write the locked `issue`'s current `description` to
     `.cadence/description-current.md` (Write tool).
  2. Invoke Bash:
     `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/promote_acceptance_criteria.py --comments <commentsFile> --description-file .cadence/description-current.md`
     Parse the JSON on stdout. (`<commentsFile>` is the `.cadence/comments.json`
     written in this step's Gather.) The helper finds the planner's latest
     `## Proposed Acceptance Criteria` comment, merges (augments) its items
     into the description's `## Acceptance Criteria` block, and emits
     `{promote, new_description, added_count, reason}`. It performs **no**
     Linear write — it only computes the new body.
  3. If `promote` is `true`: update the Linear issue's **description** to
     `new_description` via the Linear MCP (the `update_issue` / `save_issue`
     write tool the connected server exposes, with the description field).
     This is a **new bootstrap Linear-write surface** (issue-description
     update) — still performed by the bootstrap as the sole Linear writer.
     Then **re-read the issue via `get_issue`** and use that refreshed object
     as the locked `issue` from here on, so step 8 composes the implementer's
     context against the promoted AC. (This is a read-after-write refresh of
     one object — the in-memory `issue` still holds the pre-write
     description, so do **not** trust an in-memory edit. It is **not** a
     restart: nothing re-routes, re-locks, or re-picks work; the fire
     proceeds straight to step 7.)
  4. If `promote` is `false`: take no Linear write and proceed (no re-read
     needed — the in-hand `issue` is already current).

  When `plan.promote_ac` is `false`, skip this sub-phase entirely.

---

## Step 7 — Emit attempt marker

Compute the current UTC timestamp as an ISO 8601 string with second precision
ending in `Z` (example: `2026-05-10T14:23:01Z`). If the model context does not
include a reliable current time, invoke Bash to run
`date -u +%Y-%m-%dT%H:%M:%SZ` (POSIX) or, on Windows PowerShell,
`Get-Date -AsUTC -Format yyyy-MM-ddTHH:mm:ssZ` and use the output.

Build the attempt-marker comment by invoking Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind state --state <targetState> --attempt <attempt> --started-at <ISO8601>`
Post its stdout as a Linear comment verbatim. The script emits no `status`
field — this comment **is** the attempt marker counted by the Route step
(step 6) on future fires.

---

## Step 8 — Compose the Lifecycle Context block

Write the locked `issue` MCP object verbatim as JSON to `.cadence/issue.json`
(call it `issueJsonPath`) using the Write tool. Then invoke Bash:

`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/compose_lifecycle_context.py --workflow-path "${CLAUDE_PROJECT_DIR:-.}/.claude/workflow.yaml" --issue <issueJsonPath> --target-state <targetState> --attempt <attempt> --parse-comments-output <parseCommentsOutputPath>`

If the router's plan set `rework: true` (this fire entered via a gate
**rework** route), append `--rework` so the script includes the Rework
Context section.

The script reads:
- The config, by re-validating `.claude/workflow.yaml` internally
  (`--workflow-path`) — `states[<targetState>]` for `adversarial_context` /
  `linear_state` / `next`, the resolved next state for its `type` /
  `linear_state`, and `linear.team` for branch derivation.
- The issue object (`issueJsonPath`, written immediately above) —
  `identifier` / `title` / `url` / `branchName` / `priority` / `labels` /
  `description`.
- The parse-comments output (`parseCommentsOutputPath`, written in the Route
  step's Execute branch from the router's `parse_comments_output`) —
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
`prompt` parameter in step 9.

---

## Step 9 — Invoke the subagent

Look up `targetState.subagent` in the config (e.g. `planner`, `implementer`,
`reviewer`). Invoke the **Agent** tool with:

- `subagent_type`: the subagent name from config (case-sensitive, matches the
  `name` field in `.claude/agents/<subagent>.md`).
- `description`: a short string like `Cadence <targetState> for <identifier>`.
- `prompt`: the full string composed in step 8.

Run the subagent in the foreground (blocking). Capture its returned summary as
`subagentSummary`.

If the Agent tool raises an exception or the subagent returns nothing usable
(e.g. an error string framed as failure), treat this as a **failed attempt**
and skip to the **Failure path** in the Constraints section below. Do **not**
advance Linear state.

---

## Step 10 — Create the PR (if applicable), then post the subagent's summary

### Create or reuse the PR

The bootstrap owns all GitHub pull-request operations (subagents only push
branches; they never create PRs). This sub-phase runs **only when
`subagentSummary` declares a pushed branch and a PR** — i.e. the summary carries
both a `**Branch:**` line naming a real branch (not `(no-op — none created)` or
blank) **and** a `**PR title:**` line. That is the implementer's return
contract. A planner / reviewer summary, or an implementer no-op that pushed
nothing, has no such fields → **skip this entire sub-phase**, set `prUrl` to
null, and go straight to the post below.

When it does apply:

1. Read three fields out of `subagentSummary`: the branch from its
   `**Branch:**` line, the PR title from its `**PR title:**` line, and the PR
   body from its `### PR body` section (the text between that heading and the
   next `###` heading).
2. **Reuse if a PR already exists** (rework run, or an idempotent re-fire):
   call GitHub MCP `list_pull_requests` filtered to the head branch and open
   state. Some connectors expect the `head` filter as `<owner>:<branch>` rather
   than a bare branch name; if a bare-branch query returns nothing, retry
   owner-qualified. If an open PR for the branch is found, take its URL as
   `prUrl` and skip the create.
3. **Otherwise create it:** call GitHub MCP `create_pull_request` with
   `head` = the branch, `base` = the repo's default branch, and the `title` /
   `body` read above. No repo argument — the tool scopes to the bound repo.
   Take the returned PR URL as `prUrl`.
4. **Inject the URL into the summary.** Insert a line `**PR:** <prUrl>`
   immediately after `subagentSummary`'s leading `## Implementation` header, so
   the comment posted below carries the URL exactly where `parse_comments`
   discovers it (paired with the implement attempt marker from step 7). The
   rest of the summary is posted verbatim.
5. **PR-creation failure** (the `create_pull_request` call errors): treat it as
   a failed attempt and escalate. Build a failure record via Bash
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind state --state <targetState> --attempt <attempt> --status failed --error "<PR-creation error>" --subagent <subagent>`,
   post its stdout verbatim, add the `label.cadence_needs_human` label, remove
   the `label.cadence_active` label, and **exit** without advancing Linear
   state. (The branch is pushed; a human resolves the PR creation.)

### Post the summary

Post `subagentSummary` (with the `**PR:**` line injected in step 4 above, when
this fire created or reused a PR) as a Linear comment on the issue,
**verbatim**. Do not add a tracking-comment prefix; this is a plain
work-product comment intended for human readers.

If `subagentSummary` is empty or whitespace, post:
> **[Cadence]** Subagent **<subagent>** returned no summary at attempt <attempt>.

…and proceed (do **not** fail the fire — the subagent succeeded as far as we can
tell, it just produced no text).

### Bootstrap silence

Between step 9 (subagent invocation) and step 13 (exit summary), the bootstrap's
only user-facing output is the `subagentSummary` posted verbatim in this step
and the per-step Linear writes that steps 11 and 12 require. **Do not annotate
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

## Step 11 — Advance Linear state

> Step 11 runs only on the `invoke_subagent: true` path. The terminal advance
> for an opt-in `merge_on_approve` gate-approve happens earlier, in Step 6's
> **Merge on approve** sub-phase (that fire is `invoke_subagent: false` and
> never reaches Step 11) — so there is no conflict between the two.

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

## Step 12 — Release the lock

Remove the `label.cadence_active` label from the issue via Linear MCP.

---

## Step 13 — Exit

Print a one-line summary to the user:
> Cadence: <identifier> advanced from **<targetState>** → **<next>** (attempt <attempt>).

Exit. Do not loop. Do not pick up another issue.

---

## Constraints

### Side-effect ordering

- **Before step 4 (lock acquisition):** any error causes a clean exit with NO
  Linear writes. Read-only operations only.
- **After step 4:** any error must, on a best-effort basis, remove the
  `cadence_active` label before exiting. If even the label-removal fails, the
  stale-lock sweeper (`/cadence:sweep`) will clear it on its next fire.
- **Never** advance Linear state after a subagent failure (see Failure path
  below). The attempt marker from step 7 stands; the failure record records
  the outcome.

### Failure path (subagent throws / returns unusable)

If the Agent invocation in step 9 raises an exception:

1. Take the subagent's exception message as the error string.
2. Build the failure record by invoking Bash:
   `python "${CLAUDE_PROJECT_DIR:-.}"/.claude/hooks/emit_tracking_comment.py --kind state --state <targetState> --attempt <attempt> --status failed --error "<exception message>" --subagent <subagent>`
   (The script collapses newlines to spaces and truncates the error to 400
   chars itself.) Post its stdout as a Linear comment verbatim.

   This uses the same `attempt` number as the attempt marker from step 7,
   **plus** a `status: "failed"` field. It is a failure **record**, not a new
   attempt marker; the Route step (step 6) on the next fire will not count
   it.
3. Remove the `cadence_active` label.
4. Exit. The next fire will retry — `attempt` will be the same `attemptCount + 1`
   value (the marker from step 7 is what's counted, and it remains).

   On retry, the router's `attempt_count` is now `attempt` (the failed
   attempt's marker), so the new fire's `attempt = attempt + 1`. Eventually
   `attempt_count >= max_attempts_per_issue` and the Route step escalates.

### Concurrency

- Process **exactly one issue per fire.** Parallelism comes from multiple fires
  on overlapping cron intervals (each grabs a different issue via the soft lock).
- Never invoke more than one subagent per fire.

### Read-before-write discipline

Every Linear write (label add/remove, state move, comment) should be preceded
in this fire's logic by reading the relevant state — except step 3's initial
query and step 4's lock acquisition (which is itself a read-after-write check).

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
