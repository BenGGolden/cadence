<!--
  Cadence ticket template. Copy-paste into the Linear "Description"
  field, then fill in. The `## Acceptance Criteria` block is recommended
  but not required: if you leave it out, Cadence's planner proposes
  acceptance criteria for you and the bootstrap promotes them into this
  description once you approve the plan at plan_review. Authoring your own
  up front keeps the planner anchored to the outcomes you care about.
-->

## Context

One or two paragraphs. What is the user-facing or system-level problem?
What is the current behaviour? Why does it need to change?

## Acceptance Criteria

<!-- Each item must be independently verifiable from the diff + tests.
     Vague items ("works well", "is fast") are not acceptable.
     If an outcome genuinely can't be asserted by an automated test (e.g.
     "db reset applies cleanly"), tag it — `- [ ] **AC-N** — [manual-eval]
     …` — so the reviewer verifies it at the human gate instead of flagging
     a missing test. Use sparingly. -->

- [ ] **AC-1** — _Describe a specific, testable behaviour._
- [ ] **AC-2** — _Another._

## Out of scope

Anything the implementer might be tempted to do but shouldn't, in this
ticket. Reduces scope creep at review time.

## Notes / pointers

Links to related issues, screenshots, log excerpts, prior art. Optional.
