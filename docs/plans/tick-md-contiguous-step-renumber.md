# tick.md contiguous step renumber — Implementation Plan

## Context

`commands/tick.md` is the dispatch prose the harness executes for one Cadence
fire. Its sections are numbered "Step N", but the numbering has gaps:

- **Removed stubs.** Old `Step 2 — (removed in determinism P3)` and
  `Step 3 — (removed in determinism P2)` are placeholder sections kept only to
  avoid shifting later numbers ([commands/tick.md:158-169](../../commands/tick.md#L158-L169)).
- **A collapsed range.** The `route_fire.py` extraction merged four former
  steps into one section titled `Steps 8–11 — Route the fire`
  ([commands/tick.md:287](../../commands/tick.md#L287)).

This is the deferred follow-up captured in
[BACKLOG.md:12-29](../../BACKLOG.md#L12-L29): renumber the steps to a clean
contiguous sequence, dropping the stubs and the range. The determinism pass
that added `route_fire.py` deliberately deferred it to keep that PR focused on
behaviour.

**Nothing parses tick.md step numbers at runtime.** `tick.md` is prose executed
by the harness/LLM, not read by any Python code. The Python test suite tests the
helper scripts, not tick.md. So this change is a pure documentation/prose
consistency edit: **zero behavioural impact, no test should change result.** The
work is careful find-and-classify across the repo, not logic.

The user chose to **keep `Step 0`** (the dry-run pre-flight branch) and keep
`Step 1` (read/validate config) stable, renumbering only the steps after the
dropped stubs. This minimises churn — many script docstrings reference
"step 1 = validation".

## Scope

- **In scope:**
  - Renumber `commands/tick.md` section headings to `Step 0` + contiguous
    `Step 1`–`Step 13` (mapping below); delete the two removed-stub sections and
    the ranged heading.
  - Update every **live** cross-reference to a tick.md step number — inside
    tick.md itself and across the repo (hook/script docstrings, `status.md`
    prose, test comments, live `BACKLOG.md` idea references).
  - Remove the now-completed "tick.md contiguous step renumber" entry from
    `BACKLOG.md`.

- **Out of scope / explicit non-goals:**
  - **No behavioural change.** Routing logic, prose semantics, and the
    Gather→Route→Execute structure of the Route step are untouched.
  - **Do not rewrite the route decision taxonomy.** `route_fire.py` and
    `test_route_fire.py` use `Step 8 / 9 / 10 / 11` (and sub-labels `10a/10b/10c`)
    as the **internal names of the four routing decisions**, mirrored between the
    two files. These map to the *pre-extraction* prose, not to a current tick.md
    heading. Leave them exactly as they are — renaming them would force inventing
    "Step 6a/6b/6c/6d" and desync the script ↔ test parity matrix for no gain.
  - **Do not touch `CHANGELOG.md`.** It is an append-only historical record; its
    entries correctly describe the step numbers as they were when each change
    shipped. The BACKLOG's update list deliberately omits it.
  - **Leave all "old step N" historical references unchanged** (see the
    classification rule below).
  - No renumbering of the *other* commands' own steps (`status.md`, `sweep.md`,
    `init.md`, `create-ticket.md` each have their own independent `Step N`
    sequences — only their references *to tick.md* change).

## Affected areas

Primary file:

- **[commands/tick.md](../../commands/tick.md)** — section headings + all
  internal cross-references. The bulk of the work.

Cross-reference sites (live references to tick.md step numbers):

- **[templates/hooks/validate_workflow.py](../../templates/hooks/validate_workflow.py)** — docstrings at lines 5, 13, 190, 233, 298, 317.
- **[templates/hooks/compose_lifecycle_context.py](../../templates/hooks/compose_lifecycle_context.py)** — lines 5, 17 (line 6 stays; line 303 stays — taxonomy).
- **[templates/hooks/filter_candidates.py](../../templates/hooks/filter_candidates.py)** — lines 5, 8, 115, 274.
- **[templates/hooks/emit_tracking_comment.py](../../templates/hooks/emit_tracking_comment.py)** — lines 5, 6 (line 11 stays — sweep's own step).
- **[templates/hooks/classify_drift.py](../../templates/hooks/classify_drift.py)** — line 18 (lines 6, 9 stay — "old step 9" historical).
- **[templates/hooks/route_fire.py](../../templates/hooks/route_fire.py)** — lines 108, 110, 311 (lines 6, 185, 217, 227, 235, 283 stay — taxonomy).
- **[commands/status.md](../../commands/status.md)** — lines 24, 36 (its own Steps 1–5 stay).
- **[scripts/merge_settings_permissions.py](../../scripts/merge_settings_permissions.py)** — lines 55, 56, 57 (tick refs only; sweep/status refs stay).
- **[BACKLOG.md](../../BACKLOG.md)** — remove entry at lines 12-29; update live idea references at lines 78, 138, 246-247, 476-477.

Test comments (no assertion depends on these — cosmetic, but in scope per the
BACKLOG's "grep before and after"):

- **[tests/test_filter_candidates.py](../../tests/test_filter_candidates.py)** — line 274.
- **[tests/test_validate_workflow.py](../../tests/test_validate_workflow.py)** — line 389 (tick ref only).
- **[tests/test_compose_lifecycle_context.py](../../tests/test_compose_lifecycle_context.py)** — line 279.
- **[tests/test_route_fire.py](../../tests/test_route_fire.py)** — line 161 (lines 5, 130 stay — taxonomy).

Stays as-is (do not edit):

- **`CHANGELOG.md`** — historical record.
- **`CLAUDE.md` line 34** — "the old steps 8–11 decision core" is an explicit
  historical description of what `route_fire.py` subsumed. Leave it.
- **`classify_gate.py` line 6** — "the verdict routing of the old tick.md
  step 10" — "old"-prefixed historical. Leave.
- **`README.md` lines 151, 353** — these are README's *own* internal step
  references ("step 5 above", "Step 4"), not tick.md references. Leave.

## The renumbering map (authoritative)

`Step 0` and `Step 1` are unchanged. The two removed stubs are deleted. Everything
from old `Step 4` onward shifts down to close the gaps; the `Steps 8–11` range
becomes a single `Step 6`.

| Old heading | New heading | Section title |
|---|---|---|
| Step 0 | **Step 0** | Dry-run branch *(unchanged)* |
| Step 1 | **Step 1** | Read and validate config *(unchanged)* |
| Step 2 — (removed in P3) | **DELETE** | — |
| Step 3 — (removed in P2) | **DELETE** | — |
| Step 4 | **Step 2** | The validator's derived maps feed downstream scripts |
| Step 5 | **Step 3** | Pick work |
| Step 6 | **Step 4** | Acquire soft lock (with race retry) |
| Step 7 | **Step 5** | Move issue out of pickup state (if applicable) |
| Steps 8–11 | **Step 6** | Route the fire (Gather → Route → Execute) |
| Step 12 | **Step 7** | Emit attempt marker |
| Step 13 | **Step 8** | Compose the Lifecycle Context block |
| Step 14 | **Step 9** | Invoke the subagent |
| Step 15 | **Step 10** | Post the subagent's summary |
| Step 16 | **Step 11** | Advance Linear state |
| Step 17 | **Step 12** | Release the lock |
| Step 18 | **Step 13** | Exit |

Quick lookup for reference-fixing (old → new), excluding unchanged 0/1:
`4→2, 5→3, 6→4, 7→5, (8–11)→6, 12→7, 13→8, 14→9, 15→10, 16→11, 17→12, 18→13`.

## Classification rule (apply to every `step N` occurrence)

When you hit a step-number reference, classify it before editing:

1. **LIVE** — points at a current tick.md section to say "X happens at / is
   invoked from / is consumed by tick.md step N." → **Renumber** per the map.
2. **HISTORICAL** — explicitly says "**old** step N", "the step-N prose",
   "used to live in steps 8–11", or otherwise names a decision in the
   pre-`route_fire` prose that no longer exists as a separate tick.md step. →
   **Leave unchanged.**
3. **ROUTE TAXONOMY** — `Step 8/9/10/11` (and `10a/10b/10c`) used inside
   `route_fire.py`, `classify_gate.py`, `classify_drift.py`, and
   `test_route_fire.py` as the *names* of the four routing decisions. →
   **Leave unchanged.** (Even when not literally prefixed "old".)
4. **OWN-STEP** — a reference to *another command's* own step sequence
   (`status.md step 4`, `sweep.md step 5`, `init.md Step 4`). → **Leave
   unchanged.** Only the *number after `tick`/`/cadence:tick`* changes.
5. **CHANGELOG** — anything in `CHANGELOG.md`. → **Leave unchanged.**

## Implementation steps

Order: do tick.md first (the source of truth), then cross-references, then the
grep audit. Each step is independently checkable.

### Step 1 — Renumber `commands/tick.md` headings and delete stubs

1. Delete the two stub sections wholesale: from `## Step 2 — (removed in
   determinism P3)` through the end of the `## Step 3 — (removed in determinism
   P2)` block (current [commands/tick.md:158-169](../../commands/tick.md#L158-L169)),
   including the explanatory italic paragraphs. Leave the `---` separators tidy
   (there should be one `---` between `Step 1` and the new `Step 2`).
2. Rewrite the headings per the map. The ranged heading
   `## Steps 8–11 — Route the fire (Gather → Route → Execute)` becomes
   `## Step 6 — Route the fire (Gather → Route → Execute)`.
3. **Do not change `Step 0` or `Step 1` headings.**

### Step 2 — Fix tick.md internal cross-references

Apply the map to every LIVE in-text reference. The complete list of lines to
change (current line numbers):

| Line | Current text fragment | Change to |
|---|---|---|
| 43 | "Emitted by step 12" | step 7 |
| 44 | "The Route step (steps 8–11) counts these." | "The Route step (step 6) counts these." |
| 77 | "the script also covers step 4's workflow-Linear-states build" | step 2 |
| 180 | "the states array by step 5's `filter_candidates.py --plan`" | step 3 |
| 281 | "the workflow-state determination in the Route step (steps 8–11)" | "(step 6)" |
| 287 | (heading — done in Step 1) | — |
| 290 | "Steps 8–11 are a single cohesive decision" | "Step 6 is a single cohesive decision" |
| 327 | "(step 13 passes `--rework` when set)" | step 8 |
| 329 | "`true` to proceed to step 12" | step 7 |
| 332 | "Step 13 needs it; see Execute." | Step 8 |
| 362 | "step 13 feeds it to `compose_lifecycle_context.py`" | step 8 |
| 364 | "continue at step 12." | step 7 |
| 380 | "the Route step (steps 8–11) on future fires." | "(step 6)" |
| 418 | "Pass it as the Agent tool's `prompt` parameter in step 14." | step 9 |
| 430 | "the full string composed in step 13." | step 8 |
| 456 | "Between step 14 (subagent invocation) and step 18 (exit summary)" | step 9 … step 13 |
| 514 | "The attempt marker from step 12 stands" | step 7 |
| 519 | "If the Agent invocation in step 14 raises an exception" | step 9 |
| 527 | "the same `attempt` number as the attempt marker from step 12" | step 7 |
| 529 | "the Route step (steps 8–11) on the next fire will not count it." | "(step 6)" |
| 533 | "(the marker from step 12 is what's counted...)" | step 7 |
| 548-549 | "except step 5's initial query and step 6's lock acquisition" | step 3 … step 4 |

References that **stay unchanged** (verify you did NOT touch these):

- **Line 59** "Step 1 (and step 0's `--evidence` call)" — both unchanged.
- **Line 76** "the dry-run substitute for step 1's live invocation" — Step 1 unchanged.
- **Lines 92 & 115** "Hold the stdout as `dryRunComposed` for step 4" and
  "`dryRunComposed` from step 3" — ⚠️ **these are references to Step 0's own
  internal 1–6 numbered sub-list (sub-item 4 = the report, sub-item 3 = the
  compose call), NOT top-level steps.** Leave them.
- **Line 120** "Do not proceed to step 1 of the live path" — Step 1 unchanged.
- **Line 173** "The validator in step 1 emits a `linear_to_workflow`..." — Step 1.
- **Lines 292-295** the `(old step 8)`, `(old step 9)`, `(old step 10: ...)`,
  `(old step 11)` parentheticals inside the Route section — HISTORICAL, leave.

### Step 3 — Fix hook/script docstrings

Apply per the classification rule. Exact edits:

- **`validate_workflow.py`**
  - L5: `tick.md step 3 (live validation)` → `tick.md step 1 (live validation)`
    (⚠️ this is a *pre-existing stale* reference — live validation moved to
    Step 1 in determinism P2; fix it to step 1, not step 2).
  - L13: `the rules in tick.md step 3 were LLM prose` → `tick.md step 1`.
  - L190: `tick.md Step 5 walks each candidate` → `Step 3`.
  - L233: `(tick.md Step 13)` → `(tick.md Step 8)`.
  - L298: `Mirrors tick.md step 4.` → `step 2`.
  - L317: `Used by tick.md step 8 (Linear column -> matched workflow state)` →
    `step 6` (the matched-state lookup now lives in the Route step).
  - L7 (`step 0`), L9 (`sweep.md step 1`, `status.md step 1`) — leave.
- **`compose_lifecycle_context.py`**
  - L5: `commands/tick.md step 13` → `step 8`.
  - L17: `whose only consumer was step 13` → `step 8`.
  - L6 (`step 0`) — leave. L303 (`step 10c`) — leave (route sub-label taxonomy).
- **`filter_candidates.py`**
  - L5: `commands/tick.md step 5` → `step 3`.
  - L8: `step 5 used to iterate` → `step 3`.
  - L115: `Per tick.md step 5` → `step 3`.
  - L274: `step 14 would look up the subagent` → `step 9`.
- **`emit_tracking_comment.py`**
  - L5: `commands/tick.md step 12 (attempt marker)` → `step 7`.
  - L6: `commands/tick.md step 16 (waiting gate comment)` → `step 11`.
  - L11 (`sweep.md step 5`) — leave.
- **`classify_drift.py`**
  - L18: `step 16 emits no fresh tracking comment` → `step 11`.
  - L6 (`old tick.md step 9`), L9 (`the step-9 prose`) — leave (historical).
- **`route_fire.py`**
  - L108: `a file for step 13's compose_lifecycle_context` → `step 8`.
  - L110: `so step 13 needs no second parse` → `step 8`.
  - L311: help text `(after step 7)` → `(after step 5)`.
  - L6, L185, L217, L227, L235, L283 — leave (route taxonomy / "used to live").
- **`scripts/merge_settings_permissions.py`**
  - L55: `(tick step 5, sweep step 3, status step 3)` → `tick step 3` (sweep/status unchanged).
  - L56: `... in tick step 10` → `tick step 6` (re-reads happen in the Route Gather phase).
  - L57: `(tick step 9, status step 4)` → `tick step 6` (comment fetch is in the Route Gather phase; status unchanged).
- **`classify_gate.py`** L6 — leave (historical "old tick.md step 10").

### Step 4 — Fix `status.md` references to tick.md

- L24: `The same set /cadence:tick step 4 builds.` → `step 2`.
- L36: `/cadence:tick's Route step (steps 8–11) counts.` → `(step 6)`.
- L39: "Route step does not count it" — no number, leave.
- status.md's own `Step 1`–`Step 5` headings and self-references — leave.

### Step 5 — Fix test comments

These are comments only; no assertion reads them, so the suite passes regardless.
Update for consistency:

- `test_filter_candidates.py` L274: `step 14 would have no subagent` → `step 9`.
- `test_validate_workflow.py` L389: `tick.md step 8 and status.md step 2` →
  `tick.md step 6 and status.md step 2`.
- `test_compose_lifecycle_context.py` L279: `e.g. step 9 ran with rework_context`
  → `step 6` (the parse now happens in the Route step).
- `test_route_fire.py` L161: `step 13 reuses this without re-parsing` → `step 8`.
  - L5 (`old tick.md steps 8–11`) and L130 (`step 8: unmapped`) — leave (taxonomy).

### Step 6 — Update `BACKLOG.md`

- Remove the completed entry `## tick.md contiguous step renumber (follow-up to
  route_fire extraction)` and its body ([BACKLOG.md:12-29](../../BACKLOG.md#L12-L29)),
  including the trailing `---` so no orphaned separator remains.
- Update live tick.md references inside other backlog ideas:
  - L78: `[tick.md step 10b](./commands/tick.md)` → reword to
    `[tick.md's Route step](./commands/tick.md)` (the approve route is a Route
    sub-decision; there is no standalone "step 10b" heading).
  - L138: `during /cadence:tick step 1-5` → `step 1-3` (old 1–5 pre-pickup span
    is now steps 1–3; the lock is acquired at the new Step 4).
  - L246-247: `Add a step 17.5 in [commands/tick.md]` / `invoked from step 18` →
    `step 12.5` / `step 13`.
  - L476-477: `how step 10 dispatches` → `how the Route step dispatches`;
    `the prose edit in step 5 break the cap walk` → `step 3`.

### Step 7 — Grep audit

Run the audit commands (below). Confirm no stale LIVE reference remains and that
the only surviving `Steps 8–11` / high-numbered hits are CHANGELOG history,
route taxonomy, or "old step N" historical strings.

## Commit & PR plan

Per repo convention (recent history is `refactor: …` commits landed via numbered
PRs #25–#29; the global rule is **no AI-attribution trailer**) and the project's
feature-branch-via-PR workflow:

- **Branch:** `refactor/tick-step-renumber` off `main`.
- **One commit** is appropriate — this is a single cohesive, mechanical change
  with no behavioural boundary to split on. Suggested message:
  `refactor: renumber tick.md steps to a contiguous sequence`
  (optionally a body line: "Drops the removed-stub sections and the 8–11 range;
  moves all live cross-references in lockstep. No behavioural change.").
- **One PR** into `main`. CI (`validate.yml`) will run the manifest schema check,
  the command-frontmatter check, and the Python test suite — all expected to pass
  unchanged.

## Docs to update

- **`BACKLOG.md`** — covered in implementation Step 6 (remove the completed
  entry; fix live references). This *is* part of the change, not a separate doc
  task.
- **`CHANGELOG.md`** — add a new entry under the current unreleased/top section
  recording the renumber (the repo keeps a detailed CHANGELOG; match the existing
  entry style — a `### Changed` / refactor bullet noting "no behavioural
  change"). **Do not edit existing historical entries.**
- **`CLAUDE.md`, `README.md`, `GUIDEPOSTS.md`, `MIGRATION.md`** — None. CLAUDE.md
  line 34's "old steps 8–11" is intentionally historical; README/GUIDEPOSTS/
  MIGRATION carry no live tick.md step-number references.

## Acceptance Criteria

- [ ] **AC-1** — `commands/tick.md` section headings are `Step 0` followed by a
  contiguous `Step 1` … `Step 13`, with no `Step N — (removed …)` stub sections
  and no ranged `Steps 8–11` heading.
- [ ] **AC-2** — The two removed-stub sections (old Step 2 and Step 3) are
  deleted entirely, with no orphaned `---` separators left behind.
- [ ] **AC-3** — The former `Steps 8–11 — Route the fire` section is now
  `## Step 6 — Route the fire (Gather → Route → Execute)`, and its body's
  `(old step 8)` … `(old step 11)` parentheticals are retained verbatim.
- [ ] **AC-4** — Every LIVE internal cross-reference in `commands/tick.md` points
  to the correct new number per the map (all rows in implementation Step 2's
  "change to" column applied).
- [ ] **AC-5** — Step 0's internal sub-list references ("`dryRunComposed` from
  step 3", "for step 4") and the "old step N" parentheticals in the Route section
  are unchanged.
- [ ] **AC-6** — Live `tick.md step N` references in `validate_workflow.py`,
  `compose_lifecycle_context.py`, `filter_candidates.py`, `emit_tracking_comment.py`,
  `classify_drift.py`, `route_fire.py` (non-taxonomy lines), `status.md`, and
  `merge_settings_permissions.py` are renumbered per the map; the stale
  `validate_workflow.py` "step 3 (live validation)" is corrected to step 1.
- [ ] **AC-7** — The route decision taxonomy (`Step 8/9/10/11`, `10a/b/c`) in
  `route_fire.py`, `classify_gate.py`, `classify_drift.py`, and
  `test_route_fire.py`, all "old step N" historical strings repo-wide, and the
  entirety of `CHANGELOG.md`'s existing entries are unchanged.
- [ ] **AC-8** — `BACKLOG.md`'s "tick.md contiguous step renumber" entry is
  removed and its remaining live tick.md step references are updated/reworded.
- [ ] **AC-9** — `python -m unittest discover -s tests -v` passes (unchanged from
  before the edit).
- [ ] **AC-10** — The grep audit (Verification commands) surfaces no stale LIVE
  reference: the only remaining `Steps 8–11` / `(removed in determinism` /
  high-numbered tick step hits are CHANGELOG history, route taxonomy, or
  explicitly "old step N" historical text.

## Testing

### Unit tests

**No new or changed tests.** This change adds no behaviour and touches no code
path the suite exercises — the step numbers in `route_fire.py`/`tick.md` are not
parsed by any test. The existing suite is run only as a regression guard
(AC-9): it must continue to pass with identical results. Do **not** add tests
that assert on tick.md prose content — that would couple the suite to wording the
project deliberately keeps test-free.

### Manual testing

1. **Read tick.md top to bottom.** Confirm the headings read `Step 0, 1, 2, …,
   13` with no gaps and no stubs, and that the narrative still flows (the Route
   section now reads "Step 6 is a single cohesive decision…").
2. **Spot-check three cross-references resolve:** e.g. the Failure path's "the
   attempt marker from step 7" should point at the renamed "Step 7 — Emit attempt
   marker"; "continue at step 7" in the Route Execute branch likewise.
3. **Confirm Step 0 sub-list refs untouched:** the dry-run section's
   "`dryRunComposed` from step 3 … for step 4" still reads as sub-item
   references, not top-level steps.
4. **Dry-run smoke (optional, requires a consumer repo with `.claude/`):**
   the renumber is prose-only, so a `/cadence:tick dry-run` against an initialized
   repo should behave identically — no functional assertion, just confirmation
   the prose is still coherent to execute.

### Verification commands

Run from the repo root (`c:\Code\Cadence`). The user's shell is Git-for-Windows
bash / PowerShell; these are the repo's actual commands (there is no
`Makefile`/`pyproject.toml`/`package.json` — CI in
[.github/workflows/validate.yml](../../.github/workflows/validate.yml) drives
everything):

```bash
# Regression: full Python suite must pass unchanged (AC-9)
python -m unittest discover -s tests -v

# Audit: these should return ONLY historical/taxonomy/CHANGELOG hits (AC-10)
# 1. No surviving removed-stub headings or ranged heading in tick.md:
grep -nE "removed in determinism|Steps 8.11 . Route" commands/tick.md
#    → expect: no matches

# 2. No high-numbered live tick step refs left in tick.md (12-18 are gone):
grep -nE "step 1[2-8]|step [89]" commands/tick.md
#    → expect: only the "(old step 8|9|10|11)" parentheticals in the Route section

# 3. Repo-wide sweep of every tick step reference — eyeball each against the
#    classification rule:
grep -rnE "tick(\.md)?[^.]{0,40}[Ss]tep ?[0-9]+|[Ss]teps? ?8.11" \
  commands templates scripts tests BACKLOG.md CLAUDE.md README.md
#    → every hit is either renumbered, "old step N", route taxonomy
#      (route_fire/test_route_fire/classify_*), or another command's own step.
```

There is no separate lint/type-check/build step in this repo. The
command-frontmatter CI check only validates YAML frontmatter keys (untouched by
this change); you can replicate it if desired, but it is not affected.

## Risks & open questions

- **Risk: mis-classifying a reference.** The live/historical/taxonomy distinction
  is the only real subtlety. The per-line tables above pre-resolve every known
  site; the grep audit (AC-10) is the backstop. If a *new* `step N` reference is
  found that isn't in the tables, classify it with the rule before editing.
- **Gotcha (already flagged): Step 0's internal sub-list.** Lines 92 and 115 of
  tick.md say "for step 4" / "from step 3" but mean Step 0's own numbered
  sub-items, not top-level steps. Do not renumber them.
- **Pre-existing staleness corrected:** `validate_workflow.py:5,13` already
  pointed at "step 3 (live validation)" from a numbering two refactors old; this
  plan corrects them to step 1. Flag if you find other pre-existing stale refs
  that don't fit the map — note them rather than guessing.
- **Decision recorded:** the route taxonomy (`Step 8/9/10/11`) is intentionally
  *not* renumbered. If a reviewer questions why `route_fire.py` says "Step 8"
  while tick.md no longer has one, point them at the Scope non-goal — those are
  decision names mirrored by `test_route_fire.py`, not tick.md pointers.
- **Open question (low stakes):** whether to add a CHANGELOG entry at all for a
  no-behaviour doc refactor. The repo's CHANGELOG is unusually detailed and logs
  prior prose-only refactors, so this plan recommends adding one; if the
  maintainer prefers to skip it for pure-doc changes, drop that doc task.
