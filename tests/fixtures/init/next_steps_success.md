Cadence initialised.

Files written:
  .claude/workflow.yaml
  .claude/prompts/global.md
  .claude/ticket-template.md
  .claude/agents/planner.md
  .claude/agents/implementer.md
  .claude/agents/reviewer.md
  .claude/hooks/validate_tracking_json.py
  .claude/hooks/validate_workflow_on_prompt.py
  .claude/hooks/audit_linear_writes.py
  .claude/hooks/validate_workflow.py
  .claude/hooks/_common.py
  .claude/hooks/parse_comments.py
  .claude/hooks/emit_tracking_comment.py
  .claude/hooks/compose_lifecycle_context.py
  .claude/hooks/filter_candidates.py
  .claude/hooks/render_status_report.py
  .claude/hooks/render_sweep_report.py
  .claude/commands/cadence/tick.md
  .claude/commands/cadence/sweep.md
  .claude/commands/cadence/status.md
  .claude/settings.json (Cadence hook entries merged in)
  .claude/settings.local.json (Linear MCP allowlist merged in)

Gate labels:
  Create two Linear labels — `cadence-approve` and `cadence-rework` —
  alongside the existing `cadence-active` / `cadence-needs-human`. A
  reviewer adds one of these to an issue sitting in a gate's waiting
  column to signal approve/rework on the next /cadence:tick fire.
  Recommended: put both labels into a Linear label group so the picker
  renders the verdict as a single-select control.

Permissions for /schedule routines (paste into the routine's permissions panel):
  Detected Linear MCP namespace: linear

  mcp__linear__list_issues
  mcp__linear__get_issue
  mcp__linear__list_comments
  mcp__linear__create_comment
  mcp__linear__save_comment
  mcp__linear__update_issue
  mcp__linear__save_issue
  mcp__linear__add_label
  mcp__linear__remove_label

Cloud /schedule routines do NOT read .claude/settings.local.json, so the
allowlist above is required on the routine even if step 4 already wrote
your local copy.

Next steps:
  1. Edit .claude/workflow.yaml to point at your Linear team/project and
     set the Linear state names that map to each workflow stage. Every
     linear_state value must correspond to a real column on your Linear
     board.
  2. Edit .claude/prompts/global.md with the always-on instructions you
     want every Cadence subagent to receive (coding standards, repo
     conventions, secrets-handling rules).
  3. Tune .claude/agents/{planner,implementer,reviewer}.md — model, tools,
     and system prompt.
  4. Pick an invocation mode:
       • Remote: create a /schedule routine running /cadence:tick
         every minute, with Linear MCP and GH_TOKEN configured on it. Add
         a second routine for /cadence:sweep every 15 minutes. Paste the
         permissions block above into the routine's permissions panel.
       • Local: from an interactive Claude Code session in this repo, run
         `claude /loop 1m /cadence:tick` (after `gh auth login`).
  5. Smoke test with /cadence:tick dry-run before going live.

To create well-formed tickets, run `/cadence:create-ticket` in your
local Claude Code session and paste the output into Linear's New
Issue form. The planner subagent will refuse tickets that lack an
`## Acceptance Criteria` block.

See the plugin README for the full Consumer Setup walkthrough.
