#!/usr/bin/env python3
"""Merge Cadence's hook entries into a consumer's .claude/settings.json.

Plugin-only helper — invoked from `commands/init.md` step 4. Not scaffolded
to the consumer; it lives in `scripts/` and is called by `/cadence:init`.

Idempotent. If the consumer's settings.json already has Cadence hook entries
(identified by referencing `/.claude/hooks/{validate_tracking_json,
validate_workflow_on_prompt,audit_linear_writes}.py`), they are replaced rather
than duplicated. Non-Cadence hook entries are left untouched.

CLI:
  python merge_settings_hooks.py --settings-path PATH --template-path PATH

Exit codes:
  0  success (settings.json written or already up-to-date)
  1  bad input (template unreadable, settings.json unreadable)
  3  internal error
"""

import argparse
import json
import sys
from pathlib import Path

CADENCE_HOOK_SCRIPTS = (
    "validate_tracking_json.py",
    "validate_workflow_on_prompt.py",
    "audit_linear_writes.py",
)

EVENTS = ("PreToolUse", "UserPromptSubmit", "PostToolUse")


def _command_targets_cadence(cmd):
    if not isinstance(cmd, str):
        return False
    if "/.claude/hooks/" not in cmd.replace("\\", "/"):
        return False
    return any(name in cmd for name in CADENCE_HOOK_SCRIPTS)


def _entry_is_cadence(entry):
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    for h in hooks:
        if isinstance(h, dict) and _command_targets_cadence(h.get("command")):
            return True
    return False


def _merge(existing, template):
    """Return a new settings dict with Cadence entries merged in.

    Strategy: drop every existing event entry whose command list targets one of
    the Cadence hook scripts, then append the template's entries verbatim. This
    keeps non-Cadence hook entries intact and makes re-init purely idempotent.
    """
    merged = json.loads(json.dumps(existing))  # deep copy via JSON round-trip
    merged.setdefault("hooks", {})
    template_hooks = template.get("hooks") or {}

    for event in EVENTS:
        template_entries = template_hooks.get(event) or []
        existing_entries = merged["hooks"].get(event) or []
        kept = [e for e in existing_entries if not _entry_is_cadence(e)]
        merged["hooks"][event] = kept + template_entries
        if not merged["hooks"][event]:
            del merged["hooks"][event]

    return merged


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings-path", required=True)
    ap.add_argument("--template-path", required=True)
    args = ap.parse_args()

    template_path = Path(args.template_path)
    settings_path = Path(args.settings_path)

    if not template_path.is_file():
        print(f"Cadence: template not found: {template_path}", file=sys.stderr)
        sys.exit(1)
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Cadence: could not read template {template_path}: {e}", file=sys.stderr)
        sys.exit(1)

    if settings_path.is_file():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(
                f"Cadence: could not parse {settings_path}: {e}\n"
                "Refusing to overwrite. Fix or move the file and re-run /cadence:init.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not isinstance(existing, dict):
            print(
                f"Cadence: {settings_path} is not a JSON object. Refusing to overwrite.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        existing = {}

    merged = _merge(existing, template)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Cadence: hooks merged into {settings_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
