# Guideposts for orchestrating coding agents off a tracker

These are design principles for any system that drives coding agents from a
work tracker — issues moving through states, agents doing the work, humans
approving at gates. They describe what actually moves output quality (as
opposed to operational completeness), independent of how any particular system
implements it.

They are goals to strive toward, not a compliance checklist. A deliberately
lightweight implementation may satisfy some only partially; that is a
trade-off, not a defect. Where a principle is illustrated with how a real
system does — or doesn't do — something, the illustration is not part of the
goal; another implementation may reach it a different way.

Three implementations are referenced for contrast: the
[Symphony spec](https://github.com/openai/symphony/blob/main/SPEC.md),
[Stokowski](https://github.com/Sugar-Coffee/stokowski), and Cadence.

---

## 1. Ticket quality is upstream of everything else

A perfect orchestrator cannot compensate for a vague ticket. If "done" cannot
be checked against something written down before the work started, then "done"
is whatever the agent decides it is — unfalsifiable. The ticket is the contract
the agent is held to, not a hint about where to start, and everything
downstream inherits its precision or its ambiguity.

The strongest mechanism is a set of acceptance criteria that are each
independently verifiable, that the agent is forced to read, mark, and self-check
before declaring the work complete. The way to get there can be a dedicated
ticket-creation flow that refuses to file ambiguous work, or a standing rule
that rejects it on sight.

In practice: Stokowski encodes this as an acceptance-criteria JSON block the
agent must mark off before it can claim completion; both it and Cadence ship a
ticket-creation command. The Symphony spec leaves ticket quality to the author.

## 2. Split the workflow into separate stages

One agent run from ticket all the way to a finished change is the wrong shape.
Investigation, implementation, and review are different kinds of work — different
concerns, different ideal models, different context needs. Lumping them into a
single session loses focus and burns tokens carrying context that two of the
three phases never needed. Splitting them lets each phase start clean and lets
you match a model to the task.

In practice: both Cadence and Stokowski separate investigate/plan from implement
from review, picking a model per stage — a stronger reasoning model for
investigation, a faster one for execution, ideally a different provider for
review. The Symphony spec describes a single ticket-to-PR run, which is its
biggest gap.

## 3. Adversarial review with no shared context

An agent reviewing its own work defends its own choices — it is sycophantic
toward the thread that produced the code. A review is only worth running if it
can find what the implementation missed, which means it has to start without the
implementer's context, assumptions, and rationalizations. The more independent
the reviewer — fresh session, different model, different provider — the more it
catches. Treat review as adversarial, not collaborative.

In practice: Stokowski runs its code-review stage with a fresh session that
explicitly drops the prior thread. Pushing further toward a different model or
provider strengthens it.

## 4. Humans gate where machines can't judge

Some questions are not automatable: did the agent solve the right problem, in
the right shape, with judgment a stakeholder would endorse? A machine can check
that tests pass; it cannot check that the work was worth doing or built the way
the team would want. A human gate at the points where those questions arise is
not a bottleneck — it is the safety valve that catches drift before it compounds
into wasted budget or a wrong merge.

In practice: place gates after investigation (before implementation spends the
budget), after implementation (before merge), and optionally before merge after
an automated review. A rework cap prevents infinite human-agent ping-pong;
exceeding it escalates with a "needs human" signal. Both Cadence and Stokowski
build such gates; the Symphony spec doesn't, and is weaker for it.

## 5. Rework carries the rejection forward

When a gate sends work back, the rejection is the most valuable signal the
system produces: a specific judgment about what is wrong. Re-running the stage
blind throws that away — it wastes budget and tends to reproduce the same
mistake, because the agent has no reason to choose differently the second time.
Every rework cycle should re-enter with the rejection captured as explicit
input, aimed at the objection rather than starting cold.

This is the difference between a retry counter and a feedback loop. A bare cap
(#4) stops infinite ping-pong but does nothing to make each bounce more likely
to land; carrying the reason forward is what makes the next attempt better. The
two compose: carry the reason so attempts improve, cap the count so they can't
run forever.

In practice: when Cadence reworks an issue it injects a "Rework Context" section
into the next subagent's prompt, quoting the reviewer's comments verbatim, so the
attempt addresses the feedback instead of guessing at why it was sent back.

## 6. Keep all durable state in the tracker

State, locks, attempt history, and the audit trail can all live in the tracker
itself rather than in a separate store. When durable state lives where the work
already lives, you get restart-survivability for free, legibility to humans
(they look at the tracker, not a custom dashboard), drift reconciliation as a
natural operation, and no infrastructure to host. Every piece of durable state
should answer the question "where does this live in the tracker?" — if the
answer is "in memory" or "in a sqlite file," a crash loses it and a human can't
see it.

How cleanly this maps depends on the tracker. Some state has an obvious home —
workflow states are columns. The rest has to be mapped onto whatever primitives
the tracker exposes, and where the tracker lacks a native mechanism the system
improvises one. A tracker with a built-in locking or claim concept can use it
directly; one without forces a stand-in.

In practice: Cadence keeps all of this in Linear. Columns map naturally to
workflow states, but Linear offers no native lock and no native per-attempt
history, so Cadence improvises — a label stands in for the lock, and tracking
comments carry attempt history. Those are workarounds for missing primitives,
not the ideal shape of the principle; on a tracker that provided locking or
attempt records natively, the right move would be to use those instead.

## 7. Every operation must be safe to re-run

A one-shot, externally-triggered run can die anywhere — after pushing a branch
but before opening the PR, after moving the column but before releasing the lock,
after posting some of its comments but not the rest. The only sane recovery model
is "run it again," which holds only if every operation is idempotent: reconcile
current state before acting, reuse an artifact that already exists rather than
create a duplicate, and treat "already in the target state" as success, not
error.

Designed in from the start this is nearly free; bolted on after the first crash
it is a rewrite, because act-then-record has to become reconcile-act-record
everywhere. It pairs with #6: because each run reconstructs what it needs from
the tracker rather than from a resident process holding state in memory, there is
nothing to repair after a crash — recovery is just firing again.

In practice: on rework Cadence reuses the already-open PR instead of opening a
second, and reconciles drift at the start of every tick before it acts — so a
tick that died halfway leaves nothing a re-run can't sort out.

## 8. Mechanical guardrails beat agent discipline

Anything that depends on the agent remembering will eventually fail. Move the
guarantee out of the agent's discretion and into the harness, so it holds
regardless of whether the agent thought of it. Each guardrail converts "the
agent might forget X" into "X is enforced."

Common forms:

- **Hooks** (`before_run` / `after_run`) that always run typecheck, lint, and
  tests, regardless of whether the agent invoked them.
- **A single writer for the tracker.** Subagents return summaries; one
  orchestrator posts them — no hallucinated comments, one consistent shape, one
  audit point. The same orchestrator owns any external side-effect coupled to a
  state transition — opening the change-proposal artifact a step produced, or
  merging it once it is approved. Keep those side-effects in the orchestrator:
  executed once, read-before-write, with a defined escalation on failure, not
  delegated to an agent. The agent produces the work; the orchestrator publishes
  and lands it.
- **Permission scoping to the actual tools used**, not a whole "read-only"
  category.
- **Acceptance criteria the agent must explicitly mark verified** before
  transitioning.
- **Deterministic branch and PR naming** so humans can find the work.

In practice: the single-writer pattern is Cadence's "bootstrap is the sole
tracker-writer," with the bootstrap also owning PR creation and merge.

## 9. Prefer deterministic code to agent prose

Anything mechanical — parsing a structured comment, validating config,
formatting a string, merging JSON — a small script does faster, cheaper, and the
same way every time. Agentic systems are tempted to lean on prose for everything
("the model can figure it out"), trading reproducibility, cost, and
reviewability for flexibility they rarely need. Reserve model calls for what
genuinely needs judgment: investigating an unfamiliar codebase, weighing two
designs, writing the code itself. Everything else should be code — reproducible
across model versions, free at inference time, and reviewable as code rather
than as a prompt's emergent behavior.

This is distinct from #8: guardrails are about what the agent shouldn't be
trusted to remember; determinism is about what shouldn't be re-derived by a
model at all when a short script will do.

In practice: Cadence parses tracking comments, validates its workflow file,
formats audit lines, and merges settings with small scripts. Each could have
been a paragraph of dispatch prose; none should be.

## 10. Isolate agent work so a bad run is discardable

An agent loose on the canonical checkout can corrupt it — a half-finished edit, a
bad merge, two concurrent runs colliding on the same files. Give each run its own
disposable workspace (a worktree, branch, or sandbox) that it may mutate freely
and that can be thrown away whole if the run fails or turns hostile. The goal is
containment, not cleanup: a discarded workspace needs no unwinding, and the trunk
everyone else depends on is never the thing at risk.

This is distinct from the single-writer rule (#8): that governs who may write to
the tracker; this governs where the code changes happen. The blast radius of any
one run should be a throwaway artifact, never shared state.

In practice: Cadence runs each subagent in its own git worktree, gitignored from
the main checkout, so concurrent fires and failed attempts can't disturb the
trunk or each other.

## 11. Build for forensic debugging

Things break in ways the operator wasn't watching. The system has to be
reconstructable after the fact.

- **An audit log of every tracker write.** Every comment, label change, and
  state transition should be reconstructable later. A tracker that keeps a
  durable native activity history already provides this; where it doesn't, a
  write-time hook that appends an out-of-band log closes the gap.
- **A dry-run mode** that validates config and renders the prompt without side
  effects, ideally surfacing the validation evidence rather than just a verdict.
- **Caps and escalation** — never let an issue retry forever.
- **Drift reconciliation every run.** Humans will move issues out of band; a
  system that ignores this breaks in production within a week.
- **Failure records distinct from attempt markers.** A failed attempt still
  counts as an attempt happening, but recording the failure shouldn't
  double-count against the cap — the distinction keeps cap arithmetic accurate.

In practice: Cadence's dry-run renders the prompt with "show your work"
validation evidence; an attempt cap plus a needs-human label is its escalation;
it keeps failure records separate from attempt markers for cap accuracy.

## 12. Make the codebase self-describing for agents

Agent output quality is roughly proportional to how self-describing the codebase
is — this is the "harness engineering" idea, and it is load-bearing. A thorough
top-level agent guide, rule files for known footguns, conventions docs, and a
maintained build log are not nice-to-haves; they are the difference between
agents that follow your conventions and agents that invent their own. Treat them
as first-class engineering artifacts with the same review bar as code. Keep
agent-only instructions (headless mode, no slash commands) separate from
interactive ones, or they bleed into day-to-day sessions.

In practice: Stokowski and Cadence separate agent-facing prompts into a dedicated
directory so they don't leak into interactive use.

---

## What to explicitly avoid optimizing for

A few anti-goals — the optimizations that look like improvements and aren't.

- **Throughput.** More concurrent agents is not more software shipped. Per-state
  caps should be set for *coordination* (don't let two reviewers create a merge
  conflict), not for maximum parallelism. Cadence's "one issue per fire" is a
  strict version of this and worth keeping.
- **End-to-end autonomy.** Removing the human gates is the obvious "improvement"
  that ruins the system. The gates *are* the quality mechanism for the things
  machines can't judge.
- **A custom UI/dashboard.** The tracker and the code host are already the UIs.
  A dashboard is a maintenance burden that pulls effort away from the
  agent-quality work that matters. Stokowski ships one; Cadence deliberately
  doesn't.
- **An expressive workflow DSL.** A workflow DSL (domain-specific language) is
  the configuration language that defines the states, transitions, gates, and
  per-stage settings — the declarative spec that says "investigation flows to
  implementation, which needs approval before review." The temptation is to make
  it ever more expressive (conditionals, loops, custom hooks, computed fields)
  until it becomes a programming language in its own right. The right amount is
  "small enough that the workflow definition fits on one screen and a new
  contributor can read it in 30 seconds." Beyond that, expressiveness in the
  definition starts trading off against legibility for humans. The Symphony spec
  has no DSL and is weaker for it; Stokowski has a rich one; Cadence has a small
  one.

---

The through-line: **the system is a quality harness around the agent, not a
replacement for human judgment.** The implementations that recognize that
produce good software; the ones that try to remove the humans don't.
