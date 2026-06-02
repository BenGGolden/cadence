#!/usr/bin/env python3
"""Render the `/cadence:uninstall` Linear-cleanup checklist.

Plugin-only helper — invoked from `commands/uninstall.md` Step 5. Not
scaffolded to the consumer; lives in scripts/ alongside `render_next_steps.py`,
whose shape it mirrors (force UTF-8 stdout at import; the block is a
module-level `_TEMPLATE`; `render()` returns it; `main()` prints it).

Cadence deliberately never touches Linear (the plugin manages files; the
consumer manages Linear — the same boundary the rest of the plugin keeps). So
the file/settings side of uninstall is automated, but the Linear side is a
printed checklist the operator works through by hand. There are no
interpolation points today; the block is kept in a script rather than embedded
in the command's prose per GUIDEPOSTS #7 (text the command emits is
deterministic code's job — the init handoff sets this precedent).

CLI:
  python render_uninstall_steps.py

Exit codes:
  0  success — checklist printed on stdout
"""

import io
import sys

# The block uses em dashes (—) and bullets (•); on Windows the default stdout
# encoding is cp1252 and emitting them mangles the output. Force UTF-8 so the
# rendered block survives Windows shells (matches render_next_steps.py).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  newline="")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  newline="")

_TEMPLATE = """\
Cadence files removed.

Linear cleanup (manual — Cadence never touches your Linear workspace):

  Cadence manages files; you manage Linear. The plugin made no changes to
  your Linear board on install and makes none on uninstall, so the items
  below are yours to do by hand once you're sure no in-flight issue still
  depends on them.

  1. Labels. Once no issue still carries them, delete the four Cadence
     labels:
       • cadence-active        (the soft lock added during a fire)
       • cadence-needs-human   (the escalation flag)
       • cadence-approve        (the gate verdict label)
       • cadence-rework         (the gate verdict label)
     Check each label's issue count in Linear first; deleting a label that
     is still applied removes it from those issues.

  2. Workflow columns. The board columns you mapped in
     .claude/workflow.yaml (one per workflow stage) are no longer driven by
     Cadence. If Cadence was their only consumer, you can remove or
     repurpose them; if your team also uses them, leave them be.

  3. Nothing else. Cadence created no Linear projects, cycles, or
     integrations — there is no API token or webhook to revoke.
"""


def render():
    return _TEMPLATE


def main():
    sys.stdout.write(render())
    sys.exit(0)


if __name__ == "__main__":
    main()
