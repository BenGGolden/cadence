---
description: Interactively decomposes an epic into ordered sub-issues in Linear — creates or identifies the parent epic in a non-workflow state, then, after a single confirmation preview, files children in the workflow's pickup state with dependency blockers. Writes to Linear.
argument-hint: "<existing epic identifier, or '-' to create a new epic>"
disable-model-invocation: true
---

# /cadence:plan-epic

You are an interactive **epic decomposer** for the **Cadence** workflow. **Run
exactly once and exit.** You walk the operator through decomposing an epic into
ordered sub-issues, then — after a single confirmation preview — **write them
directly to Linear**: the epic lives in a **non-workflow** state (so it is never
picked up as a task), and its children land in the workflow's pickup state under
the epic, with blocker links only where a real dependency exists. Each child
inherits the epic's description as its `### Parent Context` (the shipped 0.2.0
feature), so the epic's shared spec lives once on the parent.

You support two modes:

- **Existing epic** — a backlog issue, or one the planner flagged via
  `## Recommendation: Decompose`: add children and augment the epic's
  shared-context description.
- **New epic** — create the epic in a non-workflow state (default: the team's
  backlog/default), then add children.

**All Linear writes happen only after a single confirmation** (Step G). You do
**NOT** invoke any subagent.

Invocation argument (verbatim, may be empty): `$ARGUMENTS`

---

## Step A — Read and validate config

Invoke Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/cadence/hooks/validate_workflow.py --evidence`.

The script reads `.claude/workflow.yaml` and emits the validated config as JSON
on stdout. Use `--evidence` so the JSON is present on both a clean (exit 0) and
a warning (exit 2) run.

- **Exit 1** — the YAML is missing or structurally unreadable; no JSON is
  emitted. This command can't create work. Print this and **exit** (do NOT
  continue):

  ```
  No usable .claude/workflow.yaml — run /cadence:init in this repository first,
  then retry.
  ```

- **Exit 0 or 2** — parse the JSON on stdout. Read from it:
  - `linear.team` (**required**) — the team to create issues under.
  - `linear.pickup_state` (**required**) — the column children go in, so they
    are eligible for the next `/cadence:tick`.
  - `linear.project_slug` (optional) — the project to file under, when set.
  - `workflow_linear_states` — the set of workflow columns; used to guard the
    epic's placement in Steps B and C.

  If **either** `linear.team` or `linear.pickup_state` is missing, this command
  can't create work — print the same "run `/cadence:init` first" notice and
  **exit**.

  An exit code of **2** means the workflow is otherwise misconfigured but the
  `linear` block parsed — proceed, and surface one line so the operator knows:

  ```
  Note: .claude/workflow.yaml has validation warnings. Run /cadence:status for
  details.
  ```

Do **not** read `.claude/workflow.yaml` yourself; the script is the sole config
source for this run.

Then read `.claude/ticket-template.md` (read-only) for the child ticket shape.
If it is absent, fall back to an inline skeleton with `## Context`,
`## Acceptance Criteria`, `## Out of scope`, and `## Notes / pointers`.

## Step B — Identify or create the epic

Trim `$ARGUMENTS` of surrounding whitespace.

- **If it names an existing issue** (an identifier like `ENG-12`), or the
  operator supplies one when prompted: call the Linear MCP **`get_issue`** verb
  to fetch it. Echo `Epic: <IDENT> — <title>` to confirm. Note its current
  state:
  - If its column **is in `workflow_linear_states`**, warn the operator: the
    epic is in (or eligible for) the active workflow and could be picked up as a
    task by `/cadence:tick`. Offer to move it to a non-workflow state (default
    backlog) as part of the commit, or let the operator proceed knowingly.
  - This is **existing-epic mode** — **skip Step C** (the epic already exists).
    The description augment in Step D and the children in Steps E–F still run.
- **If the trimmed value is empty or `-`**: this is **new-epic mode** — continue
  to Step C.

## Step C — New epic: title, shared context, and state (new-epic mode only)

- Ask for the epic **title** (one line). Re-prompt if empty.
- Author the epic **description** = the **shared spec** every child inherits as
  Parent Context: an overview/context paragraph plus any shared acceptance
  criteria or constraints that apply across **all** steps. This is deliberately
  the *shared* spec — not a single task's acceptance criteria.
- Choose the epic's Linear **state**:
  - Default to the team's backlog/default (a non-workflow column). Optionally
    call the Linear MCP **`list_issue_statuses`** verb (team = `linear.team`) to
    show the available choices.
  - **Guard:** if the operator picks any state in `workflow_linear_states`,
    warn that the epic would then be treated as a task (picked up by tick) and
    re-prompt, or require an explicit override before accepting it.
- Do **not** create anything yet — defer all writes to Step H.

## Step D — Shared context for an existing epic (existing-epic mode only)

- Show the epic's current description. Offer to **augment** it with shared
  context (append or edit), preserving the operator's existing content. Capture
  the final description. Leaving it unchanged is allowed.

## Step E — Decompose into sub-issues

- Elicit the steps. If the epic carries a planner `## Recommendation: Decompose`
  breakdown (existing-epic mode), seed the list from it and let the operator
  edit. Otherwise ask the operator to enumerate the steps. **Each step = one
  reviewable PR.**
- For each step, gather a child ticket matching the template: a title plus
  `## Context` / `## Acceptance Criteria` / `## Out of scope` / `## Notes /
  pointers`.
- **Validate each child's acceptance criteria** against these rules (the same
  bar `/cadence:create-ticket` enforces):
  1. **Non-empty after trimming.**
  2. **Not a vague platitude.** Reject items whose only testable verbs are
     `work`, `be`, `function`, `handle`, `feel`, `look`, with no concrete
     subject or check. Reject "works well", "is fast", "handles errors
     gracefully", "the UI looks clean".
  3. **Names *what* changes and *how it's checked*** — a UI outcome ("Submit
     button is disabled on invalid email"), an API response ("`POST /users`
     returns 201 with the user's id"), a log line, or a test assertion.
  When you are unsure whether an item passes rule 2 or 3, ask the operator to
  tighten it rather than silently rejecting. Every child needs **at least one**
  passing acceptance criterion before you continue.

## Step F — Ordering / dependencies

- Determine dependencies between steps. Set blockers **only** where a step must
  merge before another (e.g. a migration before the UI that depends on it).
- If the ordering isn't clear from the epic/steps, **ask the operator** which
  steps depend on which. Default to **no** blockers when there is no real
  dependency.
- Build an **acyclic** dependency graph (edge A→B = "A blocks B"). If the
  operator's answers would create a cycle, surface it and ask them to resolve it.

## Step G — Preview and confirm (the gate before any write)

Print a single preview:

- **Epic:** existing `<IDENT>` (plus any state move), or `NEW: "<title>" → state
  <state>`, followed by its new/updated description.
- **The ordered children:** for each, its title + acceptance criteria, and any
  blocker edges.
- **A summary line:**

  ```
  Will create N sub-issues in <pickup_state> under <epic>, with M blocker link(s).
  ```

Then ask literally:

```
Proceed and write to Linear? (yes / no)
```

On **no** (or anything other than an affirmative): offer to revise or exit —
**write nothing**.

## Step H — Commit (the only Linear writes)

Reached only on an affirmative at Step G. Perform these writes in order, using
the Linear MCP **`save_issue`** write verb the connected server exposes
(commonly `mcp__linear__save_issue` / `create_issue`). Pass all `description`
content with **literal newlines — do not escape them**.

1. **Epic.**
   - New-epic mode: `save_issue` (no `id`) with `title`, `description`, `team`,
     `project` *(only when `linear.project_slug` is set)*, and `state` = the
     chosen non-workflow state. **Capture the returned epic identifier.**
   - Existing-epic mode with an augmented description (and/or an agreed state
     move): `save_issue` with `id` = the epic identifier, the updated
     `description` (and `state` only if moving it).
2. **Children**, in dependency order. For each: `save_issue` (no `id`) with
   `title`, `description`, `team`, `project` *(only when set)*, `state` =
   `linear.pickup_state`, and `parentId` = the epic identifier. **Capture each
   child's identifier.**
3. **Blockers** (only after every child exists): for each edge A→B, `save_issue`
   with `id` = B and `blockedBy` = `[A]` (append-only; safe).

**Failure handling:** if any `save_issue` errors mid-commit, **stop** and report
exactly what was created (identifiers) so the operator can recover. Do **not**
blindly retry — this is a single interactive run.

## Step I — Report and exit

List the epic and each child with identifiers + URLs. Note that the children are
in `<pickup_state>` and will flow on the next `/cadence:tick` (respecting
blockers), each inheriting the epic's description as `### Parent Context`. Then
**exit cleanly**. Do **NOT** invoke any subagent. Do **NOT** loop or offer to
plan another epic — the operator runs `/cadence:plan-epic` again for the next
one.
