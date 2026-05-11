# Cadence — end-to-end smoke checklist

A single-page manual checklist for validating the Cadence plugin
against a real (throwaway) Linear project before turning it loose on
production work. Run this once per consuming repo. Expect ~30 minutes
elapsed; most of it is waiting for cron fires.

This is **manual** by design — there is no automated test suite that
talks to Linear. The audit trail of comments left on the throwaway
project after this run is the verification.

---

## Prerequisites

- [ ] A throwaway Linear team with a project you can pollute (call it
      `CADENCE-TEST`). Empty is fine.
- [ ] Linear MCP server configured locally and on your `/schedule`
      routine, with **write access** scoped to this team only.
- [ ] A throwaway GitHub repo (or a feature branch in a sandbox repo).
      `gh auth login` succeeded locally; `GH_TOKEN` configured on the
      `/schedule` routine.
- [ ] Claude Code with plugin support, plus the Cadence plugin loaded
      (`claude --plugin-dir /path/to/cadence` or an installed
      marketplace listing).
- [ ] Linear board has the six default columns set up:
      `Backlog`, `Planning`, `Implementing`, `In Review`, `Approved`,
      `Needs Rework`, `Done`. (Adjust as you go if you rename in
      `workflow.yaml`.)
- [ ] Two Linear labels exist: `cadence-active`, `cadence-needs-human`.

---

## Phase 1 — Scaffold and validate (no Linear writes)

1. [ ] `cd` into the throwaway consumer repo (NOT the Cadence plugin
       repo). Open a Claude Code session.
2. [ ] Run `/cadence:init`.
       **Expect**: `.claude/workflow.yaml`, `.claude/prompts/global.md`,
       `.claude/agents/{planner,implementer,reviewer}.md` are created.
       Next-steps instructions are printed.
3. [ ] Run `/cadence:init` again (no `--force`).
       **Expect**: refuses to overwrite, names the existing path, exits.
4. [ ] Edit `.claude/workflow.yaml` — fill in `linear.team`,
       `linear.project_slug` (your throwaway project), and leave the
       rest at defaults.
5. [ ] Run `/cadence:tick dry-run`.
       **Expect**: validation `passed`, the workflow-Linear-states set
       is printed, a Lifecycle Context block for a hypothetical
       `EXAMPLE-1` entry-state issue is composed and printed, ending
       with `DRY RUN — no side effects.`
6. [ ] Introduce a deliberate validation error (e.g. set
       `states.plan.linear_state` to the same value as
       `states.implement.linear_state`).
       Run `/cadence:tick dry-run`.
       **Expect**: an error naming both states and the duplicated
       string. No Linear writes. Revert the edit before continuing.

---

## Phase 2 — Single issue through the happy path

7. [ ] In Linear, create issue `CADENCE-TEST-1` in `Backlog` with a
       small, realistic description (e.g. "Add a `--version` flag to
       the CLI"). Set priority `Medium` (2).
8. [ ] Run `/cadence:tick` interactively (one-shot, not
       looped).
       **Expect**:
       - The issue moves `Backlog → Planning`.
       - `cadence-active` label is added during the fire and removed
         at the end.
       - One `<!-- cadence:state {"state":"plan","attempt":1,...} -->`
         tracking comment is posted.
       - One plain comment is posted with the planner subagent's
         Markdown summary.
       - The issue moves `Planning → Implementing` at end of fire.
       - The runtime prints
         `Cadence: CADENCE-TEST-1 advanced from plan → implement (attempt 1).`
9. [ ] Run `/cadence:tick` again.
       **Expect**:
       - The issue moves `Implementing → In Review` at end of fire.
       - A plain comment with the implementer's Markdown summary,
         **including a PR URL**.
       - A `<!-- cadence:gate {"state":"review","status":"waiting"} -->`
         comment is posted.
       - A new PR is open in GitHub against the throwaway repo's
         default branch.
10. [ ] Run `/cadence:tick` again.
        **Expect**: the gate is waiting; the fire releases the lock and
        exits with `No eligible issues.` or moves on to another issue
        (depending on the queue). The Linear column does not change.

---

## Phase 3 — Gate approval

11. [ ] In Linear, manually move `CADENCE-TEST-1` from `In Review` to
        `Approved`.
12. [ ] Run `/cadence:tick`.
        **Expect**:
        - The issue moves `Approved → Done` (the terminal state in the
          default workflow).
        - No subagent is invoked (terminal target, per step 10b).
        - `cadence-active` label is removed.
        - No new tracking comment is posted (the Linear state change is
          the audit record).
13. [ ] Verify the PR is still open (Cadence does not auto-merge — the
        human approves the gate, the human merges the PR).

---

## Phase 4 — Gate rework

14. [ ] In Linear, create issue `CADENCE-TEST-2` in `Backlog`. Run
        `/cadence:tick` twice to drive it to `In Review`
        (Planning fire, then Implementing fire — same shape as
        Phase 2).
15. [ ] In Linear, post a human comment on `CADENCE-TEST-2` explaining
        what to change ("please rename `foo` to `bar`"). Then move the
        issue from `In Review` to `Needs Rework`.
16. [ ] Run `/cadence:tick`.
        **Expect**:
        - A `<!-- cadence:gate {"state":"review","status":"rework",
          "rework_to":"implement"} -->` comment is posted.
        - The issue moves `Needs Rework → Implementing`.
        - The implementer subagent is invoked **with rework context in
          its Lifecycle Context block** — verify by inspecting the
          subagent's summary comment that the rework feedback was
          addressed.
        - The implementer pushes additional commits to the **existing**
          PR branch (no new PR, no force-push).
        - At end of fire, the issue moves `Implementing → In Review`
          again.
17. [ ] In Linear, approve the issue (move to `Approved`). Run
        `/cadence:tick` once more — should land in `Done`.

---

## Phase 5 — Attempt cap (negative path)

18. [ ] Create issue `CADENCE-TEST-3` with a description designed to
        make the planner fail repeatedly — easiest is to make the
        description empty or nonsensical, but a more honest test is to
        require a tool the subagent doesn't have (e.g. ask it to call
        a private API it has no credentials for).
19. [ ] Run `/cadence:tick` three times in a row.
        **Expect**:
        - Three attempt markers
          (`<!-- cadence:state {"state":"plan","attempt":N,...} -->`)
          accumulate.
        - Three failure records
          (`<!-- cadence:state {"status":"failed",...} -->`) accumulate.
        - Linear state stays in `Planning` (failure path does not
          advance state).
20. [ ] Run `/cadence:tick` a fourth time.
        **Expect**:
        - A `[Cadence] Max attempts (3) reached at state plan` plain
          comment is posted.
        - The `cadence-needs-human` label is added.
        - The `cadence-active` label is removed.
        - The issue is now excluded from future picks until a human
          removes `cadence-needs-human`.

---

## Phase 6 — Sweeper

21. [ ] In Linear, manually add the `cadence-active` label to a
        `Backlog` issue (any one). Wait until that issue's
        `updatedAt` is older than `limits.stale_after_minutes`
        (default 30 — for testing, temporarily set
        `stale_after_minutes: 1` in `workflow.yaml`).
22. [ ] Run `/cadence:sweep`.
        **Expect**:
        - The `cadence-active` label is removed.
        - A `<!-- cadence:sweep ... -->` comment is posted on the
          issue.
        - The summary report names the issue under **Cleared**.
23. [ ] Run `/cadence:sweep` again.
        **Expect**: `No cadence-active locks found.` Idempotent.

---

## Phase 7 — Status reporter

24. [ ] Run `/cadence:status`.
        **Expect**:
        - The Markdown report renders with the rows from Phases 1–6
          (the closed issues in `Done`, the needs-human-flagged one in
          `Planning` with 🛑, etc.).
        - Per-state summary counts add up to the row count.
        - Footer: `Read-only — no Linear writes performed.`
25. [ ] No new comments, labels, or state changes appear in Linear
        after the status run. (Confirm by spot-checking one or two
        issues.)

---

## Phase 8 — Drift reconciliation

26. [ ] Pick any in-flight issue (or create one and drive it to
        `Implementing`). Manually drag it back to `Planning` in
        Linear.
27. [ ] Run `/cadence:tick`.
        **Expect**:
        - A `<!-- cadence:reconcile {...} -->` comment is posted
          noting the drift.
        - The fire proceeds **from Linear's state** (`plan`), not the
          state recorded in the last attempt marker.

---

## Phase 9 — Cleanup

28. [ ] Archive or delete the throwaway Linear issues.
29. [ ] Close the open PR(s) in the throwaway GitHub repo.
30. [ ] Revert `limits.stale_after_minutes` to the production value if
        you changed it.
31. [ ] If you ran this against a routine, pause or delete the routine
        before pointing it at a real project.

---

## What to do if a phase fails

- **Validation / config errors**: read the error message — it names
  the offending key. Fix `workflow.yaml` and re-run the phase. No
  reset needed.
- **Subagent crashes mid-fire**: check the failure record comment for
  the error. Most common causes are missing MCP / credentials, hitting
  a tool that isn't allowed by the subagent's `tools:` list, or the
  subagent's prompt being inconsistent with the lifecycle contract
  ("you should post to Linear yourself" instructions left over from
  a prior iteration).
- **Linear state stuck**: check labels. `cadence-active` blocks
  pickup; `cadence-needs-human` excludes the issue entirely. Remove
  manually if you've fixed the root cause.
- **Stale lock that the sweeper isn't catching**: check
  `limits.stale_after_minutes` value, and check the issue's
  `updatedAt`. Sweeper compares against `updatedAt`, not against the
  label's age.

Once all nine phases pass, the plugin is validated against this
consumer repo + Linear project combo. Wire the same `/schedule`
routine at a real project and ship it.
