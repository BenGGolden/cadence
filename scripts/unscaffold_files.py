#!/usr/bin/env python3
"""Reverse the Cadence scaffold for `/cadence:uninstall`.

Plugin-only helper — invoked from `commands/uninstall.md` Step 2. Not
scaffolded to the consumer; lives in `scripts/` like its mirror image
`scaffold_files.py`, whose `SCAFFOLD_PLAN` it imports as the single source of
truth for what to remove (never re-list the files here).

Removal policy, per the plan's policy column:
  - `plugin-owned`  → delete unconditionally (they're plugin executables).
  - `user-config`   → delete only with `--force`; otherwise leave in place and
                      list (the operator may have edited/committed these).

It also removes the `.cadence/` runtime scratch dir wholesale, and removes the
Cadence-created `.claude/` subdirectories **only when they end up empty**
(deepest-first), never `.claude/` itself.

The dry-run preview is computed by the same `_compute_plan` the real run uses,
so `--dry-run` is a faithful prediction — including which empty dirs would be
pruned.

CLI:
  python unscaffold_files.py [--dry-run] [--force]

The current working directory is the consumer repo root (matching the
commands/init.md Step 1 invariant); destinations are resolved relative to it.

Exit codes:
  0  removed successfully (including dry-run and idempotent re-run)
  1  a deletion failed on a real run (best-effort: other deletions still run;
     the failing path is named on stderr)
"""

import argparse
import io
import shutil
import sys
from pathlib import Path

# Import the canonical copy plan; scaffold_files.py is a sibling module, so the
# bare import resolves when run as `python scripts/unscaffold_files.py` (the
# script's own dir is sys.path[0]) — exactly how render_next_steps.py does it.
from scaffold_files import SCAFFOLD_PLAN

_CADENCE_DIR = ".cadence"

# Cadence-created directories, deepest-first. Each is removed only if it ends
# up empty after file removal. `.claude/` itself is never touched (consumer
# owns it). `.claude/commands` is listed after its `cadence` child so the child
# is decided first.
_CANDIDATE_DIRS = (
    ".claude/commands/cadence",
    ".claude/commands",
    ".claude/hooks",
    ".claude/worktrees",
    ".claude/agents",
    ".claude/prompts",
)


def _force_utf8_stdio():
    """Force UTF-8 with no newline translation so stdout is byte-identical
    across platforms (matches scaffold_files.py). Called from main() only so
    importing this module has no stdio side effects."""
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      newline="")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                      newline="")


class _Plan:
    """The computed removal plan — pure inspection, no mutation."""

    def __init__(self):
        self.removed = []          # dest paths that will be / were deleted
        self.skipped = []          # user-config dests left in place (no --force)
        self.absent = []           # dests already gone
        self.cadence_present = False
        self.dirs_removed = []     # Cadence dirs that will be / were pruned
        self.dirs_left = []        # Cadence dirs left because non-empty


def _compute_plan(force):
    """Inspect the filesystem and return a _Plan. Mutates nothing."""
    plan = _Plan()
    removed_paths = set()

    for _, dest_rel, policy in SCAFFOLD_PLAN:
        dest = Path(dest_rel)
        if not dest.exists():
            plan.absent.append(dest_rel)
            continue
        if policy == "user-config" and not force:
            plan.skipped.append(dest_rel)
            continue
        plan.removed.append(dest_rel)
        removed_paths.add(dest)

    plan.cadence_present = Path(_CADENCE_DIR).exists()

    # Simulate empty-dir pruning, deepest-first. A dir prunes if every current
    # entry is either a file scheduled for removal or a subdir already pruned.
    pruned = set()
    for d in _CANDIDATE_DIRS:
        p = Path(d)
        if not p.is_dir():
            continue
        all_gone = True
        for entry in p.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                if entry not in pruned:
                    all_gone = False
                    break
            else:
                if entry not in removed_paths:
                    all_gone = False
                    break
        if all_gone:
            plan.dirs_removed.append(d)
            pruned.add(p)
        else:
            plan.dirs_left.append(d)

    return plan


def _apply(plan):
    """Execute the plan's deletions. Returns a list of (path, error) failures."""
    failures = []

    for dest_rel in plan.removed:
        try:
            Path(dest_rel).unlink()
        except OSError as e:
            failures.append((dest_rel, e))

    if plan.cadence_present:
        try:
            shutil.rmtree(_CADENCE_DIR)
        except OSError as e:
            failures.append((_CADENCE_DIR, e))

    for d in plan.dirs_removed:
        try:
            Path(d).rmdir()
        except OSError as e:
            failures.append((d, e))

    return failures


def _render_summary(plan, dry_run):
    """Render the byte-stable stdout summary from a computed plan."""
    p = "[dry-run] would remove" if dry_run else "removed"
    lines = [
        f"Cadence: {p} {len(plan.removed)} files "
        f"({len(plan.skipped)} user-config left in place, "
        f"{len(plan.absent)} already absent)."
    ]

    if plan.cadence_present:
        verb = "would remove" if dry_run else "removed"
        lines.append(f"  {verb} .cadence/ scratch directory")
    else:
        lines.append("  .cadence/ already absent")

    if plan.dirs_removed:
        verb = "would remove empty directories:" if dry_run else \
            "removed empty directories:"
        lines.append(verb)
        lines += [f"  {d}" for d in plan.dirs_removed]

    if plan.skipped:
        lines.append("user-config left in place (re-run with --force to remove):")
        lines += [f"  {d}" for d in plan.skipped]

    if plan.dirs_left:
        lines.append("directories left in place (not empty):")
        lines += [f"  {d}" for d in plan.dirs_left]

    return "\n".join(lines) + "\n"


def unscaffold(force, dry_run):
    """Run the removal. Returns the exit code; writes its own stdout/stderr."""
    plan = _compute_plan(force)

    failures = []
    if not dry_run:
        failures = _apply(plan)

    sys.stdout.write(_render_summary(plan, dry_run))

    if failures:
        for path, err in failures:
            print(f"Cadence: could not remove {path}: {err}", file=sys.stderr)
        return 1
    return 0


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="Also remove user-config files (workflow.yaml, "
                         "prompts/global.md, agents/*.md, ticket-template.md).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be removed and delete nothing.")
    args = ap.parse_args()

    sys.exit(unscaffold(args.force, args.dry_run))


if __name__ == "__main__":
    main()
