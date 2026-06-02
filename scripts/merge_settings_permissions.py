#!/usr/bin/env python3
"""Merge Cadence's Linear-MCP permission allowlist into .claude/settings.local.json.

Plugin-only helper — invoked from commands/init.md after the hooks merge.
Not scaffolded to the consumer; it lives in scripts/ (init-time only) and
is called by /cadence:init.

The bootstrap calls a small, fixed set of Linear MCP verbs (list_issues,
get_issue, list_comments, save_comment / create_comment, save_issue /
update_issue, add_label, remove_label). In Mode A (/schedule) every one of
those must be pre-allowed on the routine — a cloud routine has no human
to answer a permission prompt and will hang on the first ask. In Mode B
(/loop) pre-allowing the same set lets the loop run unattended without
stalling.

The caller (commands/init.md) is responsible for detecting which Linear
MCP namespace the consumer has installed (commonly `linear`,
`linear-server`, or `claude_ai_Linear`). It passes that name in via
`--namespace`, and this script writes one `mcp__<namespace>__<verb>`
entry per canonical Cadence verb into `permissions.allow`.

Use `--print-only` to skip the write and just emit the allowlist on
stdout — the `commands/init.md` step that surfaces a copy-pasteable
block for /schedule operators uses this mode (cloud routines do not
read local settings, so the operator pastes the block into the
routine's permissions panel by hand).

Idempotent: re-running replaces Cadence-owned entries rather than
duplicating, and leaves unrelated entries the consumer added by hand
alone. "Cadence-owned" is identified by a heuristic — entries matching
`mcp__<namespace-containing-"linear"-case-insensitive>__<canonical-verb>`.

`--remove` reverses the merge for `/cadence:uninstall`: it strips every
Cadence-owned allowlist entry (reusing the same `_is_cadence_owned`
heuristic, which matches any `linear`-containing namespace — so removal is
namespace-agnostic and needs no `--namespace`) and prunes empty `allow` /
`permissions` containers, deleting the file if it reduces to `{}`. Unrelated
allow entries survive byte-for-byte; an unparseable file is refused (exit 1).

CLI:
  python merge_settings_permissions.py --settings-path PATH --namespace NAME
  python merge_settings_permissions.py --print-only --namespace NAME
  python merge_settings_permissions.py --settings-path PATH --remove [--dry-run]

Exit codes:
  0  success (settings.local.json written / deleted / up-to-date / printed)
  1  bad input (missing/empty namespace, settings.local.json unreadable)
  3  internal error
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Canonical Cadence verb list. Different Linear MCP servers expose these
# under slightly different names; we write every variant so whichever
# names the detected namespace actually exposes are pre-allowed. Entries
# for verbs that do not exist on the server are harmless — the permission
# system tolerates them.
CADENCE_VERBS = (
    "list_issues",     # read: candidate query (tick step 3, sweep step 3, status step 3)
    "get_issue",       # read: lock recheck, issue refetch, label re-read in tick step 6
    "list_comments",   # read: tracking-comment parsing (tick step 6, status step 4)
    "create_comment",  # write: post tracking comments and subagent summaries
    "save_comment",    # write: same intent, alternate name on some namespaces
    "update_issue",    # write: state changes and label mutations on some namespaces
    "save_issue",      # write: same intent, alternate name on some namespaces
    "add_label",       # write: cadence_active lock, cadence_needs_human escalation
    "remove_label",    # write: cadence_active release, verdict-label removal
)


def _generate_entries(namespace):
    return [f"mcp__{namespace}__{verb}" for verb in CADENCE_VERBS]


_OWNED_ENTRY_RE = re.compile(r"^mcp__([A-Za-z0-9_-]+)__([A-Za-z0-9_]+)$")


def _is_cadence_owned(entry):
    """Heuristic match against entries we previously wrote.

    Identifies anything shaped `mcp__<namespace>__<verb>` where the
    namespace contains "linear" (case-insensitive) and the verb is one of
    CADENCE_VERBS. This catches our prior writes across the three known
    namespaces (linear, linear-server, claude_ai_Linear) without
    disturbing unrelated entries the consumer added by hand.
    """
    if not isinstance(entry, str):
        return False
    m = _OWNED_ENTRY_RE.match(entry)
    if not m:
        return False
    namespace, verb = m.group(1), m.group(2)
    if "linear" not in namespace.lower():
        return False
    return verb in CADENCE_VERBS


def _merge_allowlist(existing_allow, new_entries):
    """Return a new allow list with Cadence-owned entries replaced.

    Drop every existing entry that matches our ownership heuristic, then
    append the new entries — skipping any that are already present in the
    kept list, since the consumer may have written one of our exact
    strings themselves.
    """
    kept = [e for e in existing_allow if not _is_cadence_owned(e)]
    kept_set = set(kept)
    appended = []
    for entry in new_entries:
        if entry in kept_set:
            continue
        appended.append(entry)
        kept_set.add(entry)
    return kept + appended


def _merge_into_settings(existing, new_entries):
    merged = json.loads(json.dumps(existing))  # deep copy via JSON round-trip
    permissions = merged.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        merged["permissions"] = permissions
    existing_allow = permissions.get("allow")
    if not isinstance(existing_allow, list):
        existing_allow = []
    permissions["allow"] = _merge_allowlist(existing_allow, new_entries)
    return merged


def _unmerge_allowlist(existing_allow):
    """Return a copy of the allow list with Cadence-owned entries dropped."""
    return [e for e in existing_allow if not _is_cadence_owned(e)]


def _unmerge_from_settings(existing):
    """Return a copy of `existing` with Cadence-owned allow entries stripped.

    Prune `allow` if it ends up empty, then `permissions` if it ends up empty,
    so a Cadence-only file collapses to `{}` (the caller deletes it). Other
    keys and unrelated allow entries are preserved.
    """
    merged = json.loads(json.dumps(existing))  # deep copy via JSON round-trip
    permissions = merged.get("permissions")
    if not isinstance(permissions, dict):
        return merged
    existing_allow = permissions.get("allow")
    if isinstance(existing_allow, list):
        kept = _unmerge_allowlist(existing_allow)
        if kept:
            permissions["allow"] = kept
        else:
            permissions.pop("allow", None)
    if not permissions:
        merged.pop("permissions", None)
    return merged


def _load_existing(settings_path):
    """Read + parse settings_path, enforcing the dict invariant (exit 1 on
    parse error / non-dict). The caller handles the missing-file case."""
    try:
        existing = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"Cadence: could not parse {settings_path}: {e}\n"
            "Refusing to overwrite. Fix or move the file and re-run.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not isinstance(existing, dict):
        print(
            f"Cadence: {settings_path} is not a JSON object. Refusing to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)
    return existing


def _run_remove(settings_path, dry_run):
    """Strip Cadence-owned allow entries from settings_path. Returns exit code."""
    if not settings_path.is_file():
        print(f"Cadence: {settings_path} not present; nothing to remove.")
        return 0

    existing = _load_existing(settings_path)
    unmerged = _unmerge_from_settings(existing)

    prefix = "[dry-run] " if dry_run else ""
    if not unmerged:
        print(f"Cadence: {prefix}removing {settings_path} "
              "(reduced to empty after stripping Cadence permissions).")
        if not dry_run:
            settings_path.unlink()
        return 0

    if unmerged == existing:
        print(f"Cadence: {prefix}no Cadence permission entries in "
              f"{settings_path}; nothing to remove.")
        return 0

    print(f"Cadence: {prefix}stripped Cadence permission entries from "
          f"{settings_path}.")
    if not dry_run:
        settings_path.write_text(
            json.dumps(unmerged, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings-path",
                    help="Path to .claude/settings.local.json (required unless --print-only).")
    ap.add_argument("--namespace",
                    help="Linear MCP namespace (e.g. linear, linear-server, "
                         "claude_ai_Linear). Required for the merge/--print-only "
                         "paths; not needed for --remove.")
    ap.add_argument("--print-only", action="store_true",
                    help="Skip writing; print the canonical allowlist on stdout.")
    ap.add_argument("--remove", action="store_true",
                    help="Unmerge: strip Cadence-owned allow entries (namespace-agnostic).")
    ap.add_argument("--dry-run", action="store_true",
                    help="With --remove, report changes without writing.")
    args = ap.parse_args()

    if args.remove:
        if not args.settings_path:
            print("Cadence: --settings-path is required with --remove.",
                  file=sys.stderr)
            sys.exit(1)
        sys.exit(_run_remove(Path(args.settings_path), args.dry_run))

    if not args.namespace:
        print("Cadence: --namespace is required unless --remove is set.",
              file=sys.stderr)
        sys.exit(1)
    namespace = (args.namespace or "").strip()
    if not namespace:
        print("Cadence: --namespace must be a non-empty string.", file=sys.stderr)
        sys.exit(1)
    if not re.match(r"^[A-Za-z0-9_-]+$", namespace):
        print(
            f"Cadence: --namespace '{namespace}' contains characters outside "
            "[A-Za-z0-9_-]; refusing to write malformed permission entries.",
            file=sys.stderr,
        )
        sys.exit(1)

    new_entries = _generate_entries(namespace)

    if args.print_only:
        for entry in new_entries:
            print(entry)
        sys.exit(0)

    if not args.settings_path:
        print("Cadence: --settings-path is required unless --print-only is set.",
              file=sys.stderr)
        sys.exit(1)

    settings_path = Path(args.settings_path)

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

    merged = _merge_into_settings(existing, new_entries)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Cadence: Linear MCP allowlist merged into {settings_path} "
          f"(namespace: {namespace}).")
    sys.exit(0)


if __name__ == "__main__":
    main()
