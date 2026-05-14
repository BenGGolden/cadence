# Guideposts for a Symphony-style system that produces good software

Captured from a review of the [Symphony spec](https://github.com/openai/symphony/blob/main/SPEC.md),
[Stokowski](https://github.com/Sugar-Coffee/stokowski), and Cadence itself. These
are design principles for any system that orchestrates coding agents off a
tracker — meant to inform [HARDENING-PLAN.md](./HARDENING-PLAN.md) iterations
and any future implementation of the concept.

Across the spec and the two implementations, the things that actually move
output quality (as opposed to operational completeness) cluster into a small
number of principles.

---

## 1. Ticket quality is upstream of everything else

A perfect orchestrator can't compensate for a vague ticket. Stokowski's strongest
contribution here is the **acceptance-criteria JSON block** — each criterion
independently verifiable, agent forced to read them, mark them, and self-check
before declaring done. Without that, "done" is unfalsifiable.

Practically: ship a `/create-ticket` flow (Stokowski has one) or a CLAUDE.md
rule that refuses ambiguous tickets. Treat the ticket as the contract, not a
hint.

## 2. Stage the work; don't lump it

The single biggest gap in Symphony spec — and the single biggest insight in
both Cadence and Stokowski — is that **one agent run from ticket-to-PR is the
wrong shape**. Investigation, implementation, and review have different
concerns, optimal models, and context needs. Lumping them produces a single
sprawling session that loses focus and burns tokens.

Practically: at minimum, separate investigate/plan from implement from review.
Pick the model per stage (Opus for reasoning, Sonnet for execution, possibly
Codex or another provider for adversarial review).

## 3. Adversarial review with no shared context

The reviewer that wrote the code is sycophantic — it defends its choices.
Stokowski's `session: fresh` for the code-review stage is a real insight: a
clean session with no prior thread, ideally a different model or provider,
catches things the implementer missed.

Practically: review stages should explicitly drop session continuity.
Different provider where you can. Treat the review as adversarial, not
collaborative.

## 4. Humans gate where machines can't judge

"Did the agent solve the right problem, in the right shape, with judgment a
stakeholder would endorse?" is not automatable. Both Cadence and Stokowski
build human gates at meaningful points; Symphony doesn't, and is weaker for
it. The gate isn't a bottleneck — it's the safety valve that catches drift
before it compounds.

Practically: gate after investigation (before implementation burns the
budget), after implementation (before merge), and possibly before merge after
automated review. Add `max_rework` to prevent infinite ping-pong; escalate
with a "needs human" label when exceeded.

## 5. The tracker IS the workflow

Cadence's strongest design call: **state, locks, attempt history, audit trail —
all live in the tracker.** No separate database, no in-memory state to lose,
no parallel UI to build. Linear columns *are* the workflow state. Tracking
comments preserve attempt history. A label is the soft lock.

This buys you: restart-survivability for free, legibility to humans (they
look at Linear, not a dashboard), drift reconciliation as a natural operation,
and no infrastructure to host.

Practically: every piece of durable state should answer the question "where
does this live in the tracker?" If the answer is "in memory" or "in a sqlite
file," reconsider.

## 6. Mechanical guardrails beat agent discipline

Anything that depends on the agent remembering will eventually fail. Bake
guardrails into the harness:

- **Hooks** (`before_run` / `after_run`) that always run typecheck, lint,
  tests — regardless of whether the agent thought to.
- **Bootstrap is the sole tracker-writer** (Cadence's pattern). Subagents
  return summaries; the bootstrap posts them. No hallucinated comments,
  consistent shape, single audit point.
- **Permission scoping per actual tool used**, not "the whole read-only
  category." Cadence's README is exemplary; treat it as the floor.
- **Acceptance criteria the agent must explicitly mark verified** before
  transitioning.
- **Deterministic branch/PR naming** so humans can find the work.

Each of these is "the agent might forget X" → "X is enforced by the harness."

## 7. Build for forensic debugging

Things will break in ways the operator wasn't watching. The system has to be
reconstructable after the fact.

- **Audit log of every tracker write** (Cadence's HARDENING-PLAN P2.3 is the
  right shape, even if not yet landed).
- **Dry-run mode** that validates config and renders the prompt without side
  effects. Cadence's `/cadence:tick dry-run` with the "show your work"
  validation evidence is the right grain.
- **Caps and escalation** — never let an issue retry forever. Cadence's
  `max_attempts_per_issue` + `cadence-needs-human` label is the right pattern.
- **Drift reconciliation every tick.** Humans WILL move issues out of band.
  A system that ignores this breaks in production within a week.
- **Failure records distinct from attempt markers** (Cadence's distinction):
  a failed attempt counts as an attempt happened, but a failure record
  doesn't double-count. This matters for cap accuracy.

## 8. The codebase has to teach the agent

This is OpenAI's "harness engineering" concept and it's load-bearing. Agent
output quality is roughly proportional to how self-describing the codebase
is. A thorough CLAUDE.md, rule files for known footguns
(`.claude/rules/agent-pitfalls.md`), conventions docs, and an actively-
maintained `docs/build-log.md` are not nice-to-haves — they're the difference
between agents that follow your conventions and agents that hallucinate their
own.

Practically: treat CLAUDE.md and rule files as first-class engineering
artifacts with PR review. Keep agent-only instructions (headless mode, no
slash commands) separate from interactive instructions, or they bleed into
your day-to-day Claude Code sessions (Stokowski's `prompts/` directory
pattern).

---

## What to explicitly avoid optimizing for

A few anti-goals that the implementations gesture at, mostly by omission:

- **Throughput.** More concurrent agents ≠ more software shipped. Per-state
  caps should be set for *coordination* (don't let two reviewers merge-
  conflict), not for max parallelism. Cadence's "one issue per fire" is a
  stricter version of this and worth keeping.
- **End-to-end autonomy.** Removing the human gates is the obvious
  "improvement" that ruins the system. The gates ARE the quality mechanism
  for the things machines can't judge.
- **A custom UI/dashboard.** Stokowski has one; Cadence explicitly doesn't.
  Linear and GitHub are already the UIs. A dashboard is a maintenance burden
  that pulls work away from the agent-quality stuff that matters.
- **An expressive workflow DSL.** Symphony has none and is weaker for it;
  Stokowski has a rich one; Cadence has a small one. The right amount is
  "small enough that the workflow file fits on one screen and a new
  contributor can read it in 30 seconds." Beyond that, complexity in the
  workflow definition starts trading off against legibility for humans.

---

## Concrete suggestions for Cadence specifically

Holding the above against the current state of Cadence:

- **The ticket-quality input is currently implicit.** Worth scaffolding an
  acceptance-criteria pattern into the `/cadence:init` output and having the
  planner subagent enforce it before implementation begins.
- **No adversarial-review stage in the default template.** The shipped
  template is plan → implement → review → done with the same model class.
  Adding an explicit code-review stage with `model: claude-opus` and
  `session: fresh` semantics (when Claude Code subagents support that —
  today the harness doesn't give you per-invocation session control the way
  Stokowski's CLI does) would be a quality win.
- **The HARDENING-PLAN P2.3 audit log is genuinely the missing piece.** Land
  it. Once you're running real work through `/schedule`, you'll want it.
- **Consider per-state concurrency caps for multi-operator setups.** Currently
  the soft lock prevents two operators clobbering one issue, but doesn't
  prevent six concurrent `implement`s when only one `review` slot makes
  sense.
- **The lifecycle context block in tick.md Step 13 is already strong** — the
  level of detail it dictates (transition info, rework context, branch
  suggestion) is exactly what subagents need pre-built rather than inferred.
  Hold that line; don't let it drift into "let the agent figure it out."

The through-line: **the system is a quality harness around the agent, not a
replacement for human judgment.** The implementations that recognize that
produce good software; the ones that try to remove the humans don't.
