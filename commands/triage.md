---
description: Interactively triages the non-blocking findings in a single Cadence ticket's comments (reviewer findings, planner risks, implementer caveats), checks each against the codebase and existing tickets, and — after one confirmation — files the wanted ones as new Backlog issues linked back to the source. Writes to Linear.
argument-hint: "<ticket identifier, e.g. ENG-5>"
disable-model-invocation: true
---

# /cadence:triage

You are an interactive **finding triager** for the **Cadence** workflow. **Run
exactly once and exit.** You operate on **one** ticket: you enumerate the
non-blocking concerns its subagents surfaced (reviewer findings ranked
`major`/`minor` and `[follow-up]`-tagged deferrals, planner "Risks / open
questions", implementer caveats), assess each for **accuracy** and **existing
coverage** — with the full-repo read and full Linear visibility the cold
reviewer never had — and then, **after a single confirmation preview**, file the
wanted ones as new **Backlog** issues linked back to the source. You do **NOT**
invoke any subagent.

**This is explicitly not automation.** The command proposes and cites evidence;
the human decides. The valuable judgment — *is this finding accurate?* and *is
it already covered?* — stays with the operator. You never create or dismiss a
finding without an explicit human decision, and you never mark a finding covered
without citing the specific ticket (and acceptance criterion) that covers it.

Invocation argument (verbatim, may be empty): `$ARGUMENTS`

Scope guardrails for the whole run:

- **One ticket per invocation.** There is no multi-ticket sweep.
- **No change to the source issue's workflow position.** No state moves, no gate
  labels. The **only** write to the *source* issue is the single triage-marker
  comment in Step 7.
- New issues land in a **non-workflow** state (Backlog), never a
  `workflow_linear_states` column — otherwise `/cadence:tick` would pick them up.

---

## Step 0 — Read and validate config

Invoke Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/cadence/hooks/validate_workflow.py --evidence`.

The script reads `.claude/workflow.yaml` and emits the validated config as JSON
on stdout. Use `--evidence` so the JSON is present on both a clean (exit 0) and
a warning (exit 2) run.

- **Exit 1, or `linear.team` / `linear.pickup_state` missing** — triage is
  inherently Linear-coupled (unlike `/cadence:create-ticket` there is no paste
  fallback: the findings, the coverage check, and the writes all require the
  live board). Print this and **exit** (do NOT continue):

  ```
  No usable .claude/workflow.yaml — run /cadence:init in this repository first,
  then retry.
  ```

- **Exit 0 or 2** — parse the JSON on stdout. Read from it:
  - `linear.team` (**required**) — the team to file new issues under.
  - `linear.pickup_state` (**required**) — checked only to confirm config is
    usable; new issues do **not** go here.
  - `linear.project_slug` (optional) — the project to file under, when set.
  - `workflow_linear_states` — the set of workflow columns. Used to keep the
    Backlog state (and any merge target) out of the active workflow.

  An exit code of **2** means the workflow is otherwise misconfigured but the
  `linear` block parsed — proceed, and surface one line so the operator knows:

  ```
  Note: .claude/workflow.yaml has validation warnings. Run /cadence:status for
  details.
  ```

Do **not** read `.claude/workflow.yaml` yourself; the script is the sole config
source for this run.

## Step 1 — Resolve the target issue

Trim `$ARGUMENTS` of surrounding whitespace.

- If the trimmed value names an identifier (e.g. `ENG-5`), use it.
- If it is empty or `-`, ask the operator literally:

  ```
  Which ticket? (identifier, e.g. ENG-5)
  ```

  Read their reply and trim it; re-prompt until you have an identifier.

Call the Linear MCP **`get_issue`** verb the connected server exposes for that
identifier. Capture its `identifier`, `title`, `url`, `description` (including
its `## Acceptance Criteria`), `state`, `parentId`, `labels`, and any
relations (`blockedBy` / `blocks` / `related`). Echo to confirm:

```
Triaging: <IDENT> — <title>
```

## Step 2 — Enumerate candidates

Fetch the issue's comments via the Linear MCP **`list_comments`** verb the
connected server exposes. Write the result **verbatim** to `.cadence/comments.json`
(the `.cadence/` scratch convention — create the directory if needed). Then run:

`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/cadence/hooks/extract_findings.py --input .cadence/comments.json`

and parse its JSON. It returns the latest paired `reviewer` / `planner` /
`implementer` outputs, the reviewer's structured `findings`, and any
`prior_triage` markers. Build the candidate list in this order:

1. **Reviewer `findings` where `follow_up` is true** — the ticket-deferred
   concerns most likely to need their own issue.
2. The **other reviewer `findings`** (`major`, then `minor`).
3. **Planner concerns** you identify by scanning `planner.body`'s
   "Risks / open questions".
4. **Implementer concerns** you identify by scanning `implementer.body`'s
   caveats / "Anything the next state will need".

The reviewer `findings` are the guaranteed-complete, structured enumeration;
planner/implementer concerns are model-scanned from their bodies (those outputs
aren't reliably sectioned) — but **every** concern you surface still gets a
recorded disposition later, so nothing is silently dropped.

If `prior_triage` is non-empty, an earlier triage already handled some findings.
Note what it created / merged / dismissed and **drop or clearly flag** those so
you don't re-propose them.

**If there are zero candidates**, say so and **exit** — nothing to triage.

## Step 3 — Gather coverage context (read-only)

To ground the coverage check in the full ticket picture the cold reviewer
lacked, read the tickets most likely to already cover a finding, in this order:

1. **The epic and its siblings.** If the source has a `parentId`, `get_issue`
   the epic (for its shared spec / acceptance criteria) and `list_issues`
   filtered by that parent for the sibling children.
2. **The source's relations** — the `blockedBy` / `blocks` / `related` issues
   captured in Step 1.
3. **A bounded keyword sweep** of the team's open issues via `list_issues`
   (scoped by `linear.project_slug` when set) or `search`, using salient terms
   from the findings.

This read is what lets you cite a *specific* covering ticket rather than guess.
It is a coverage aid, not proof: the operator makes the final call (Step 4).

## Step 4 — Assess each candidate (the core loop)

Walk the candidates **one at a time** (borrow `/cadence:create-ticket`'s "one
focused question at a time" discipline — do not batch). For each, present:

- **Source & finding** — planner / implementer / reviewer, with severity and
  `[follow-up]` where applicable, and the finding text.
- **Accuracy assessment** — read the cited file/area in the repo and say whether
  the concern is **real**, **overstated**, or a **false positive**, with a
  concrete pointer (`path:line`).
- **Coverage verdict** — **either** "covered by `<IDENT>` (its `AC-N`: …)"
  citing the specific ticket and, where applicable, the acceptance criterion,
  **or** "not covered".
- **Recommendation** — create new issue / skip (already covered) / merge into
  `<IDENT>` / your call — with a one-line reason.

**Guardrail (load-bearing):**
- Never mark a finding **covered / dismissed** without citing the **specific
  ticket (and AC)** that covers it.
- Never let the command silently drop a finding — **every** candidate ends with
  an explicit disposition chosen by the operator.

The operator picks, per finding: **create** / **skip** (already covered) /
**merge-into-existing** / **edit-then-create**. Record each disposition — and,
for skips, the cited coverer (`<IDENT>` + `AC-N`) — to carry into Steps 6–7.

## Step 5 — Draft the wanted issues

Read `.claude/ticket-template.md` (read-only) for the ticket shape; if it is
absent, fall back to an inline skeleton with `## Context`,
`## Acceptance Criteria`, `## Out of scope`, and `## Notes / pointers`.

**For each `create` (and `edit-then-create`):** draft a Cadence-shaped ticket
from the finding. Seed a candidate **title**, `## Context`, and
`## Acceptance Criteria` from the finding, then let the operator confirm or edit.
Validate every acceptance criterion against the **same AC-quality bar**
`/cadence:create-ticket` enforces:

1. **Non-empty after trimming.**
2. **Not a vague platitude.** Reject items whose only testable verbs are `work`,
   `be`, `function`, `handle`, `feel`, `look`, with no concrete subject or check
   ("works well", "handles errors gracefully", "the UI looks clean").
3. **Names *what* changes and *how it's checked*** — a UI outcome, an API
   response, a log line, or a test assertion.

When unsure whether an item passes rule 2 or 3, ask the operator to tighten it
rather than silently rejecting. **Every drafted issue needs ≥1 passing AC.**
Render each as `- [ ] **AC-N** — <criterion text>`, numbered in order.

Prepend a back-link line to the body:

```
Follow-up from <IDENT> (<url>).
```

**For each `merge-into-existing`:** capture the target `<IDENT>` and the exact
AC/note to append to its description. If the target is in a
`workflow_linear_states` column, **warn** the operator that it may be mid-flight
and confirm before choosing it.

## Step 6 — Resolve the Backlog state, preview, and confirm (the single write gate)

Resolve the create state:

- Optionally call the Linear MCP **`list_issue_statuses`** verb (team =
  `linear.team`) to see the available states.
- Default to a state named/typed **Backlog** — a **non-workflow** column.
- If no Backlog state exists, or the default falls inside
  `workflow_linear_states`, ask the operator to name a non-workflow state.
- The operator may override the state per issue in their reply.

Then print **ONE** preview:

- **Each new issue** — title + acceptance criteria + target state + back-link.
- **Each merge** — target `<IDENT>` + the AC/note to be appended.
- **The marker comment** to be posted on the source (Step 7's block).

Then ask literally:

```
Proceed and write to Linear? (yes / no)
```

On anything but an affirmative **yes** → **write nothing**; offer to revise or
exit.

## Step 7 — Commit (the only Linear writes), then report

Reached only on an affirmative at Step 6. Perform these writes in order, using
the Linear MCP **`save_issue`** write verb the connected server exposes
(commonly `mcp__linear__save_issue` / `create_issue`). Pass all `description`
and comment content with **literal newlines — do not escape them**.

1. **New issues.** For each `create` / `edit-then-create`: `save_issue` (no
   `id`) with `title`, `description` (including the back-link line), `team` =
   `linear.team`, `project` = `linear.project_slug` *(only when it is set —
   omit the field entirely otherwise)*, `state` = the resolved **Backlog**
   state, and `parentId` = the source's epic **only if** the operator chose to
   file it under the same epic. **Capture each returned identifier and URL.**
   *(Best-effort: if the server's `save_issue` exposes a `relatedTo` /
   relations field, link the new issue to the source. The body back-link is the
   reliable mechanism and is always present, so a missing relations field is not
   an error.)*
2. **Merges.** For each `merge-into-existing`: `save_issue` with `id` = the
   target and the updated `description` — read the target's current description
   first, then **append** the AC/note (append-only; never rewrite existing
   content).
3. **Marker comment.** Post **ONE** comment on the **source** issue via the
   Linear MCP `save_comment` / `create_comment` verb the connected server
   exposes:

   ```
   <!-- cadence:triage {"created":["ENG-42","ENG-43"],"merged":["ENG-7"],"dismissed":2} -->
   **Cadence triage** — reviewed N finding(s) from <IDENT>.
   - Created: ENG-42 — <title>; ENG-43 — <title>
   - Merged into: ENG-7 (added AC-N)
   - Dismissed (already covered): "<finding>" → ENG-6 AC-6; "<finding>" → …
   ```

   The HTML-comment JSON records the created / merged identifiers and the
   dismissed **count**; the prose lists every candidate's disposition. This is
   what makes "no silent drop" auditable, and what a re-run's `prior_triage`
   reads for dedup. Include every disposition — created, merged, and each
   dismissal with its cited coverer.

**This command makes no other write to the source issue — no state move, no
label change.** The marker comment is inert to `/cadence:tick` (it is a
non-tracking Cadence comment), so posting it can't disturb a source still
sitting in a gate.

**Failure handling:** if any `save_issue` / `save_comment` errors mid-commit,
**stop** and report exactly what was created (identifiers) so the operator can
recover. Do **not** blindly retry — this is a single interactive run.

Finally, list the created and merged issues with their URLs, note that the new
issues are in the **Backlog** state (so they are **not** auto-picked by
`/cadence:tick`), and **exit cleanly**. Do **NOT** invoke any subagent. Do
**NOT** loop or offer to triage another ticket — the operator runs
`/cadence:triage` again for the next one.
