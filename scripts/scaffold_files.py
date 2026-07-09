#!/usr/bin/env python3
"""Scaffold Cadence's `.claude/` tree into the consumer repo for /cadence:init.

Plugin-only helper — invoked from `commands/init.md` Step 2. Not scaffolded
to the consumer; lives in `scripts/` (init-time only).

This script owns what used to be ~140 lines of dispatch prose covering the
overwrite check, directory creation, and the per-file copy plan. Folding it
into code does two things the prose could not guarantee:

  1. **Byte-identical copies, every run.** The prose told the model to
     "Read each source, then Write the destination," which on a `--force`
     re-init invited the model to read the destination, diff it against the
     source, and *patch* — silently preserving stale plugin code. This
     script does `Path.read_bytes()` → `Path.write_bytes()` unconditionally
     (modulo the per-file policy), so plugin-owned files are always replaced
     wholesale.
  2. **One subprocess instead of ~40 harness Read/Write pairs.**

The canonical scaffold plan (`SCAFFOLD_PLAN`) is the single source of truth
for the source→destination→policy mapping. `render_next_steps.py` imports it
for its "Files written" section, so adding a hook means editing one row here.

CLI:
  python scaffold_files.py --plugin-root <path> [--force]

  --plugin-root  the directory ${CLAUDE_PLUGIN_ROOT} resolved to (required).
  --force        overwrite every destination regardless of policy.

The current working directory is the consumer repo root (matching the
commands/init.md Step 1 invariant); destinations are resolved relative to it.

Exit codes:
  0  scaffolded successfully (zero or more user-config files skipped)
  1  read/write error or bad input
  2  aborted — .claude/workflow.yaml exists and --force was not set;
     stdout carries the "already initialized" abort message
"""

import argparse
import io
import sys
from pathlib import Path


def _force_utf8_stdio():
    """Force UTF-8 with no newline translation so stdout is byte-identical
    across platforms (Windows defaults to cp1252 + \\r\\n, which would break
    the golden fixtures the tests compare against). Called from main() only so
    importing this module for SCAFFOLD_PLAN has no stdio side effects."""
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      newline="")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                      newline="")


# The one canonical copy plan. Adding a hook = adding one row.
#
#   (source_relative_to_plugin_root, destination_relative_to_cwd, policy)
#
#   policy = "plugin-owned"  → always overwrite, even without --force.
#                              Hook scripts and slash-command dispatch prose
#                              are plugin executables; keeping them in sync
#                              with the installed plugin is the point.
#   policy = "user-config"   → overwrite only with --force; skip otherwise.
#                              Templates the consumer edits and commits.
SCAFFOLD_PLAN = (
    ("templates/workflow.yaml",       ".claude/workflow.yaml",       "user-config"),
    ("templates/prompts/global.md",   ".claude/prompts/global.md",   "user-config"),
    ("templates/ticket-template.md",  ".claude/ticket-template.md",  "user-config"),
    ("templates/agents/cadence/cadence-planner.md",     ".claude/agents/cadence/cadence-planner.md",     "user-config"),
    ("templates/agents/cadence/cadence-implementer.md", ".claude/agents/cadence/cadence-implementer.md", "user-config"),
    ("templates/agents/cadence/cadence-reviewer.md",    ".claude/agents/cadence/cadence-reviewer.md",    "user-config"),
    ("templates/cadence/hooks/validate_tracking_json.py",      ".claude/cadence/hooks/validate_tracking_json.py",      "plugin-owned"),
    ("templates/cadence/hooks/validate_workflow_on_prompt.py", ".claude/cadence/hooks/validate_workflow_on_prompt.py", "plugin-owned"),
    ("templates/cadence/hooks/validate_workflow.py",           ".claude/cadence/hooks/validate_workflow.py",           "plugin-owned"),
    ("templates/cadence/hooks/_common.py",                     ".claude/cadence/hooks/_common.py",                     "plugin-owned"),
    ("templates/cadence/hooks/parse_comments.py",              ".claude/cadence/hooks/parse_comments.py",              "plugin-owned"),
    ("templates/cadence/hooks/promote_acceptance_criteria.py", ".claude/cadence/hooks/promote_acceptance_criteria.py", "plugin-owned"),
    ("templates/cadence/hooks/emit_tracking_comment.py",       ".claude/cadence/hooks/emit_tracking_comment.py",       "plugin-owned"),
    ("templates/cadence/hooks/extract_findings.py",            ".claude/cadence/hooks/extract_findings.py",            "plugin-owned"),
    ("templates/cadence/hooks/classify_drift.py",              ".claude/cadence/hooks/classify_drift.py",              "plugin-owned"),
    ("templates/cadence/hooks/classify_gate.py",               ".claude/cadence/hooks/classify_gate.py",               "plugin-owned"),
    ("templates/cadence/hooks/classify_merge.py",              ".claude/cadence/hooks/classify_merge.py",              "plugin-owned"),
    ("templates/cadence/hooks/route_fire.py",                  ".claude/cadence/hooks/route_fire.py",                  "plugin-owned"),
    ("templates/cadence/hooks/compose_lifecycle_context.py",   ".claude/cadence/hooks/compose_lifecycle_context.py",   "plugin-owned"),
    ("templates/cadence/hooks/filter_candidates.py",           ".claude/cadence/hooks/filter_candidates.py",           "plugin-owned"),
    ("templates/cadence/hooks/render_status_report.py",        ".claude/cadence/hooks/render_status_report.py",        "plugin-owned"),
    ("templates/cadence/hooks/render_sweep_report.py",         ".claude/cadence/hooks/render_sweep_report.py",         "plugin-owned"),
    ("commands/tick.md",   ".claude/commands/cadence/tick.md",   "plugin-owned"),
    ("commands/sweep.md",  ".claude/commands/cadence/sweep.md",  "plugin-owned"),
    ("commands/status.md", ".claude/commands/cadence/status.md", "plugin-owned"),
    ("templates/worktrees/.gitignore", ".claude/worktrees/.gitignore", "plugin-owned"),
)

# Directories the plan's destinations live under. Created up-front so a copy
# never fails on a missing parent.
_REQUIRED_DIRS = (
    ".claude",
    ".claude/agents/cadence",
    ".claude/prompts",
    ".claude/cadence/hooks",
    ".claude/commands/cadence",
    ".claude/worktrees",
)

_WORKFLOW_YAML = ".claude/workflow.yaml"


def _abort_message():
    """The byte-identical "already initialized" block (exit 2 stdout)."""
    lines = [
        "Cadence is already initialized: .claude/workflow.yaml exists.",
        "",
        "Re-run with --force to overwrite. This will replace:",
    ]
    lines += [f"  - {dest}" for _, dest, _ in SCAFFOLD_PLAN]
    lines += [
        "Cadence's hook entries in .claude/settings.json will also be re-merged",
        "(non-Cadence entries are preserved).",
    ]
    return "\n".join(lines) + "\n"


def _success_message(copied, skipped):
    """The exit-0 summary. `skipped` is a list of destination paths."""
    out = [f"Cadence: scaffolded {copied} files ({len(skipped)} skipped)."]
    if skipped:
        out.append("  Skipped existing (re-run with --force to overwrite):")
        out += [f"    {dest}" for dest in skipped]
    return "\n".join(out) + "\n"


def scaffold(plugin_root, force):
    """Run the scaffold. Returns the exit code; writes its own stdout/stderr."""
    if Path(_WORKFLOW_YAML).is_file() and not force:
        sys.stdout.write(_abort_message())
        return 2

    for d in _REQUIRED_DIRS:
        Path(d).mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = []
    for src_rel, dest_rel, policy in SCAFFOLD_PLAN:
        dest = Path(dest_rel)
        if policy == "user-config" and dest.is_file() and not force:
            skipped.append(dest_rel)
            continue
        src = plugin_root / src_rel
        try:
            data = src.read_bytes()
        except OSError as e:
            print(f"Cadence: could not read source {src}: {e}", file=sys.stderr)
            return 1
        try:
            dest.write_bytes(data)
        except OSError as e:
            print(f"Cadence: could not write {dest}: {e}", file=sys.stderr)
            return 1
        copied += 1

    sys.stdout.write(_success_message(copied, skipped))
    return 0


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugin-root", required=True,
                    help="Directory ${CLAUDE_PLUGIN_ROOT} resolved to.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite every destination regardless of policy.")
    args = ap.parse_args()

    sys.exit(scaffold(Path(args.plugin_root), args.force))


if __name__ == "__main__":
    main()
