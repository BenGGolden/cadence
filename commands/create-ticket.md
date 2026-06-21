---
description: Interactively drafts a Cadence-shaped Linear ticket (title + body) and, after a confirmation preview, creates it in Linear in the workflow's pickup state. Falls back to paste-ready Markdown when Linear can't be written.
argument-hint: "<one-line summary or '-' for interactive>"
disable-model-invocation: true
---

# /cadence:create-ticket

You are an interactive ticket drafter for the **Cadence** workflow. **Run
exactly once and exit.** You walk the operator through a Cadence-shaped
ticket, then — after a confirmation preview — **create it directly in
Linear** in the workflow's pickup state, so it's immediately eligible for the
next `/cadence:tick`. When direct creation isn't possible (no usable
`.claude/workflow.yaml`, missing team / pickup-state config, the operator
declines, or no Linear MCP write verb is available), you fall back to emitting
a paste-ready Markdown blob the operator files by hand. You do **NOT** invoke
any subagent.

Invocation argument (verbatim, may be empty): `$ARGUMENTS`

The goal is a well-formed ticket whose `## Acceptance Criteria` block will
survive the planner subagent's ticket-quality gate (see
`templates/agents/cadence/cadence-planner.md`). The judgment work — deciding whether an AC
is specific enough — is the point of running this in prose rather than a
script.

---

## Step 0 — Read and validate config

Invoke Bash:
`python "${CLAUDE_PROJECT_DIR:-.}"/.claude/cadence/hooks/validate_workflow.py --evidence`.

The script reads `.claude/workflow.yaml` and emits the validated config as
JSON on stdout. Its exit code, together with the parsed JSON, decides whether
this run can **create** the issue in Linear or must fall back to **paste-only
mode** (draft the ticket, end at a paste-ready block).

- **Exit 1** — the YAML is missing or structurally unreadable. No JSON is
  emitted; the create path is impossible. Note this once to the operator and
  run in **paste-only mode** for the rest of this run:

  ```
  No usable .claude/workflow.yaml — I'll draft the ticket but can't file it in
  Linear. Run /cadence:init to enable direct creation.
  ```

- **Exit 0 or 2** — parse the JSON on stdout (`--evidence` guarantees the JSON
  is present on both). Read from it:
  - `linear.team` — the team to create the issue under.
  - `linear.pickup_state` — the state to create it in, so it is immediately
    eligible for the next `/cadence:tick`.
  - `linear.project_slug` (optional) — the project to file it under, when set.

  If **both** `linear.team` and `linear.pickup_state` are present, enable the
  **create** path. If either is absent, fall back to **paste-only mode** with
  the same notice as the exit-1 case.

  An exit code of **2** means the workflow is otherwise misconfigured but the
  `linear` block parsed — keep whichever mode the team / pickup-state check
  selected, and surface one line so the operator knows:

  ```
  Note: .claude/workflow.yaml has validation warnings. Run /cadence:status for
  details.
  ```

Either way, continue to Step 1 — the template read happens in every mode. Do
**not** read `.claude/workflow.yaml` yourself; the script is the sole config
source for this run.

## Step 1 — Load the template

Read `.claude/ticket-template.md`.

- If the file is missing: print this and exit (do NOT continue):

  ```
  Cadence's ticket template is not present at .claude/ticket-template.md.
  Run /cadence:init in this repository first, then retry.
  ```

- If the file is present: keep its content in memory as the skeleton you
  will substitute operator answers into in step 4.

## Step 2 — Establish the one-line summary

Trim `$ARGUMENTS` of surrounding whitespace.

- If the trimmed value is non-empty AND not equal to `-`: treat it
  verbatim as the ticket's one-line summary. Echo it back to the operator
  in the form `Summary: <value>` so they can confirm or correct on the
  next turn before you proceed.
- Otherwise (empty or `-`): ask the operator literally:

  ```
  One-line summary?
  ```

  Read their reply, trim it, and use that as the summary. If they reply
  with an empty string, re-prompt until you have something.

## Step 3 — Walk the operator through each template section

Ask the questions below in order. After each answer, validate per the
section's rules; surface any failures back to the operator and let them
revise before moving on to the next section.

### 3a — Context

Ask:

```
What is the current behaviour? What needs to change and why?
```

Read the operator's answer. Rephrase it into a short paragraph (one or
two sentences) in your own words, then ask:

```
Captured as:

  <rephrased paragraph>

Confirm or revise? (reply "ok" to accept, or paste the revised text.)
```

If the operator replies with anything other than `ok` (case-insensitive),
treat the reply as the revised paragraph and accept it verbatim. Continue
to 3b.

### 3b — Acceptance Criteria

Ask:

```
What are the independently verifiable outcomes? Give one per line.
```

Read the operator's reply and split it into items by line, dropping blank
lines and any leading bullet markers (`-`, `*`, `1.`, `[ ]`, etc.).

For each item, in order, validate it against these rules:

1. **Not empty after trimming.** Reject empty items silently (do not
   surface them to the operator).
2. **Not a vague platitude.** Reject items whose only testable verbs are
   `work`, `be`, `function`, `handle`, `feel`, `look`, with no concrete
   subject or object. Examples of vague items to reject: "Works well",
   "Handles errors gracefully", "Feels fast", "The UI looks clean."
3. **Specifies *what* changes and *how it can be checked*.** A good item
   names a UI outcome ("Submit button is disabled on invalid email"), an
   API response ("`POST /users` returns 201 with the user's id"), a log
   line ("Emits `[auth] token refreshed` at INFO when the refresh
   succeeds"), or a test assertion ("Existing test `test_login.py::
   test_redirects_on_401` passes after the change"). If the item names
   neither a change nor a way to check, reject it.

If you are unsure whether an item passes rule 2 or 3, do **not** silently
reject — ask the operator:

```
This item may be too vague to verify from the diff:

  <item>

Can you tighten it, or confirm it's specific enough?
```

After validating all items, if any failed, list the failures back to the
operator with one-line reasons and ask them to revise. Loop until every
item passes. You need **at least one** passing item to continue; zero
passing items is a hard stop — re-prompt.

### 3c — Out of scope

Ask:

```
Anything the implementer should NOT touch in this ticket?
```

Read the answer. Accept `(none)` (case-insensitive, with or without
parentheses) as a valid answer meaning the section will render as
`(none)`. Otherwise accept the answer verbatim.

### 3d — Notes / pointers

Ask:

```
Any links or prior art? (optional — reply with "-" to skip.)
```

Read the answer. If the trimmed reply is empty or `-`, omit the section
content (render as `(none)`). Otherwise accept the answer verbatim.

## Step 4 — Render the ticket body

Substitute the operator's answers into the template skeleton from step 1:

- Replace the `## Context` body with the rephrased paragraph from 3a.
- Replace the `## Acceptance Criteria` body with the validated items from
  3b, numbered `AC-1`, `AC-2`, ... in the order the operator gave them.
  Each rendered item must take the form:

  ```
  - [ ] **AC-N** — <criterion text>
  ```

  Preserve the operator's wording verbatim — do not rephrase AC items.
- Replace the `## Out of scope` body with the answer from 3c.
- Replace the `## Notes / pointers` body with the answer from 3d.
- Strip the leading `<!--` … `-->` HTML comment (the template's
  copy-paste instructions to the human) and the in-section `<!-- ... -->`
  hints. The rendered body should contain no HTML comments.

## Step 5 — Preview, confirm, and create (or fall back to paste)

What happens here depends on the mode chosen in Step 0.

### 5a — Paste-only mode

If Step 0 left you in **paste-only mode**, skip the confirmation entirely and
go straight to the paste-ready block in 5d. You cannot file the issue; the
block is the deliverable.

### 5b — Create mode: preview and confirm

Otherwise, print the draft for review, substituting `<one-line summary>` from
step 2 and `<rendered Markdown body>` from step 4:

```
--- Cadence ticket draft ---

Title: <one-line summary>

Description:

<rendered Markdown body>

--- End ---
```

Then print where it will go (include the `, project <project>` clause only
when `linear.project_slug` is set):

```
Will create in Linear — team <team>[, project <project>], state <pickup_state>.
```

Then ask literally:

```
Create this in Linear now? (yes — create / no — give me paste-ready Markdown instead)
```

If the operator answers **no** (or anything other than an affirmative), go to
the paste-ready block in 5d. On **yes**, continue to 5c.

### 5c — Create the issue

Call the Linear MCP **`save_issue`** write verb the connected server exposes
(commonly `mcp__linear__save_issue` / `create_issue`) with:

- `title` = the one-line summary (step 2)
- `description` = the rendered Markdown body (step 4), with **literal
  newlines — do not escape them**
- `team` = `linear.team`
- `state` = `linear.pickup_state`
- `project` = `linear.project_slug` — **only when it is set**; omit the field
  entirely otherwise
- Leave `priority` and `labels` unset (deferred for v1).

On success, capture the returned identifier and URL and report, then go to
Step 6:

```
Created <IDENT> — <URL>. It will be picked up on the next /cadence:tick.
```

If the `save_issue` call errors (no write verb available, auth failure, API
error), tell the operator the create failed — one line, including the error —
then fall through to 5d so the drafted work isn't lost.

### 5d — Paste-ready fallback

Reached when the run is in **paste-only mode** (5a), the operator declined
(5b), or a create attempt errored (5c). Print exactly this, substituting
`<one-line summary>` from step 2 and `<rendered Markdown body>` from step 4:

```
--- Cadence ticket draft ---

Title: <one-line summary>

Description:

<rendered Markdown body>

--- End ---

Paste the title into Linear's "Title" field and the description into
the "Description" field. After creating, the issue is eligible for
pickup once it lands in the workflow's pickup_state column.
```

## Step 6 — Exit

Exit cleanly. The only Linear write this command may make is the single
`save_issue` from step 5c; make no other Linear writes — no labels, no
comments, no state moves. Do **NOT** invoke any subagent. Do **NOT** loop or
offer to draft another ticket. The operator runs `/cadence:create-ticket`
again for the next one.
