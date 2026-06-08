Cadence initialised.

Files written:
  .claude/workflow.yaml
  .claude/prompts/global.md
  .claude/ticket-template.md
  .claude/agents/cadence/cadence-planner.md
  .claude/agents/cadence/cadence-implementer.md
  .claude/agents/cadence/cadence-reviewer.md
  .claude/cadence/hooks/validate_tracking_json.py
  .claude/cadence/hooks/validate_workflow_on_prompt.py
  .claude/cadence/hooks/validate_workflow.py
  .claude/cadence/hooks/_common.py
  .claude/cadence/hooks/parse_comments.py
  .claude/cadence/hooks/promote_acceptance_criteria.py
  .claude/cadence/hooks/emit_tracking_comment.py
  .claude/cadence/hooks/classify_drift.py
  .claude/cadence/hooks/classify_gate.py
  .claude/cadence/hooks/route_fire.py
  .claude/cadence/hooks/compose_lifecycle_context.py
  .claude/cadence/hooks/filter_candidates.py
  .claude/cadence/hooks/render_status_report.py
  .claude/cadence/hooks/render_sweep_report.py
  .claude/commands/cadence/tick.md
  .claude/commands/cadence/sweep.md
  .claude/commands/cadence/status.md
  .claude/worktrees/.gitignore
  .claude/settings.json (Cadence hook entries merged in)

Gate labels:
  Create two Linear labels — `cadence-approve` and `cadence-rework` —
  alongside the existing `cadence-active` / `cadence-needs-human`. A
  reviewer adds one of these to an issue sitting in a gate's waiting
  column to signal approve/rework on the next /cadence:tick fire.
  Recommended: put both labels into a Linear label group so the picker
  renders the verdict as a single-select control.

Permissions for /schedule routines (paste into the routine's permissions panel):
  No Linear MCP server detected. Substitute <REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE> below with your actual namespace (see README "Linear MCP tools" for the three variants in the wild), then add each line to your .claude/settings.local.json permissions.allow array.

  mcp__REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE__list_issues
  mcp__REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE__get_issue
  mcp__REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE__list_comments
  mcp__REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE__create_comment
  mcp__REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE__save_comment
  mcp__REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE__update_issue
  mcp__REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE__save_issue
  mcp__REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE__add_label
  mcp__REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE__remove_label

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
  3. Tune .claude/agents/cadence/cadence-{planner,implementer,reviewer}.md
     — model, tools, and system prompt.
  4. Pick an invocation mode:
       • Remote: create a /schedule routine running /cadence:tick
         every minute. Add the Linear connector and bind a GitHub
         repository (the repo picker) — connector tools, including PR
         writes, are auto-allowed on the routine; no GH_TOKEN or setup
         script is needed. Add a second routine for /cadence:sweep every
         15 minutes. Paste the permissions block above into the routine's
         permissions panel.
       • Local: from an interactive Claude Code session in this repo, run
         `claude /loop 1m /cadence:tick` (with the Linear and GitHub MCP
         connectors configured in your local Claude Code).
  5. Smoke test with /cadence:tick --dry-run before going live.

To draft well-formed tickets quickly, run `/cadence:create-ticket` in
your local Claude Code session and paste the output into Linear's New
Issue form. It's optional — when a ticket lacks an
`## Acceptance Criteria` block the planner authors one as part of its
plan, which the bootstrap promotes into the description once you approve
at plan review.

See the plugin README for the full Consumer Setup walkthrough.
