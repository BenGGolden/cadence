#!/usr/bin/env python3
"""Render the `/cadence:init` "Next steps" block.

Plugin-only helper — its `render()` is called by `scripts/configure_linear.py`
(the init.md Step 4 orchestrator) after detection + the settings merges, and
it remains independently invocable as a CLI. Not scaffolded to the consumer;
lives in scripts/ (init-time only).

The block is the operator's after-init handoff: it lists every file
written, the recommended Linear-label setup, the `/schedule` permissions
block, and the next-step checklist. The dispatch prose used to carry
this as a ~70-line verbatim block with three interpolation points; the
script absorbs the block + the interpolations so the prose's job
collapses to "invoke this and print stdout."

Interpolation points:

  --settings-local-written {true|false}
      true   → include the
               `  .claude/settings.local.json (Linear MCP allowlist merged in)`
               line in the "Files written" section.
      false  → omit that line entirely.

  --permissions-detection-note '<text>'
      A single-line note describing the detection outcome. Either:
        "Detected Linear MCP namespace: <linearNamespace>"
      or:
        "No Linear MCP server detected. Substitute ..."
      The script prints it verbatim under the "Permissions for /schedule
      routines" header — line-wrap and capitalisation are the caller's
      responsibility.

  --permissions-block '<text>'
      The raw stdout from `scripts/merge_settings_permissions.py
      --print-only` — one canonical allowlist entry per line. The script
      indents each line with two spaces so it aligns with the section
      header above it.

CLI:
  python render_next_steps.py \\
      --settings-local-written true \\
      --permissions-detection-note 'Detected Linear MCP namespace: linear' \\
      --permissions-block "$(cat block.txt)"

Exit codes:
  0  success — block printed on stdout
  1  bad input (missing required arg, --settings-local-written not bool)
"""

import argparse
import io
import sys

# The block contains em dashes (—) and bullets (•); on Windows the default
# stdout encoding is cp1252 and emitting them mangles the output. Force
# UTF-8 so the rendered block survives Windows shells.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  newline="")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  newline="")

# Single source of truth for the copied-file list — the destination column of
# the scaffold plan. Adding a hook means editing one row in scaffold_files.py;
# this section follows automatically. (scaffold_files lives alongside this
# script in scripts/, so it resolves on sys.path[0] when run as a subprocess.)
from scaffold_files import SCAFFOLD_PLAN

_FILES_WRITTEN = tuple(dest for _, dest, _ in SCAFFOLD_PLAN)

_SETTINGS_JSON_LINE = ".claude/settings.json (Cadence hook entries merged in)"
_SETTINGS_LOCAL_LINE = (
    ".claude/settings.local.json (Linear MCP allowlist merged in)"
)

# The block below is the operator-facing handoff rendered at the end of
# /cadence:init (Step 4, via configure_linear.py). The `{...}` placeholders
# are substituted by render(); everything else is emitted verbatim.
_TEMPLATE = """\
Cadence initialised.

Files written:
{files_section}

Gate labels:
  Create two Linear labels — `cadence-approve` and `cadence-rework` —
  alongside the existing `cadence-active` / `cadence-needs-human`. A
  reviewer adds one of these to an issue sitting in a gate's waiting
  column to signal approve/rework on the next /cadence:tick fire.
  Recommended: put both labels into a Linear label group so the picker
  renders the verdict as a single-select control.

Permissions for /schedule routines (paste into the routine's permissions panel):
  {detection_note}

{permissions_section}

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
  3. Tune .claude/agents/{{planner,implementer,reviewer}}.md — model, tools,
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
"""


def _parse_bool(value, flag):
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    print(
        f"Cadence: {flag} must be 'true' or 'false' (got {value!r}).",
        file=sys.stderr,
    )
    sys.exit(1)


def _indent_block(block, prefix="  "):
    """Indent each non-empty line of `block` with `prefix`. Empty lines
    stay empty so multi-paragraph blocks render consistently."""
    lines = block.splitlines()
    return "\n".join(prefix + line if line else line for line in lines)


def render(settings_local_written, detection_note, permissions_block):
    files = list(_FILES_WRITTEN) + [_SETTINGS_JSON_LINE]
    if settings_local_written:
        files.append(_SETTINGS_LOCAL_LINE)

    files_section = "\n".join(f"  {f}" for f in files)
    permissions_section = _indent_block(permissions_block)

    return _TEMPLATE.format(
        files_section=files_section,
        detection_note=detection_note,
        permissions_section=permissions_section,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings-local-written", required=True,
                    help="'true' if step 4 wrote .claude/settings.local.json, "
                         "'false' otherwise.")
    ap.add_argument("--permissions-detection-note", required=True,
                    help="Single-line note describing the Linear MCP "
                         "detection outcome.")
    ap.add_argument("--permissions-block", required=True,
                    help="Raw stdout from merge_settings_permissions.py "
                         "--print-only.")
    args = ap.parse_args()

    settings_local = _parse_bool(args.settings_local_written,
                                 "--settings-local-written")

    sys.stdout.write(render(
        settings_local,
        args.permissions_detection_note,
        args.permissions_block,
    ))
    sys.exit(0)


if __name__ == "__main__":
    main()
