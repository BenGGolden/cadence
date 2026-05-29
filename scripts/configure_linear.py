#!/usr/bin/env python3
"""Configure Linear MCP access and render the /cadence:init "Next steps" block.

Plugin-only helper — invoked from `commands/init.md` as the final init step.
Not scaffolded to the consumer; lives in `scripts/` (init-time only).

This is the orchestrator that folds what used to be Steps 4c + 5 of the
dispatch prose into one process. The prose carried: a shell pipe, exit-code
branching on the detector, capture of the namespace into a variable,
threading of `permissionsBlock` / `detectionNote` / `settings-local-written`
across two more invocations, and a conditional write-vs-skip of
`.claude/settings.local.json`. All of that was model-driven plumbing that
re-ran on every fire. It now lives here:

  1. Detect the Linear MCP namespace — scan `claude mcp list` stdout (piped
     in on stdin), falling back to a `.mcp.json` scan. (Reuses
     detect_linear_mcp_namespace's scanners.)
  2. On a hit, merge the canonical Cadence allowlist into
     `.claude/settings.local.json` (reuses merge_settings_permissions's
     merge logic) and build the permissions block under the real namespace.
  3. On no hit, take the placeholder path internally — no settings.local
     write, a substitute-this note, and a placeholder namespace in the
     block. Detection failure is not an error exit.
  4. Render the operator-facing "Next steps" block (reuses
     render_next_steps.render) and print it on stdout.

The pipe is byte-for-byte what the prose did today
(`claude mcp list | python configure_linear.py`); this script never spawns
a nested CLI child of its own.

CLI:
  claude mcp list 2>/dev/null \\
    | python configure_linear.py \\
        --plugin-root <path> \\
        --settings-local-path .claude/settings.local.json \\
        --mcp-json-path .mcp.json

Exit codes:
  0  success — "Next steps" block printed on stdout (detection may have
     succeeded or fallen back to the placeholder path; both are success)
  1  internal error rendering the block
"""

import argparse
import io
import json
import sys
from pathlib import Path

# Decode `claude mcp list` stdin as UTF-8 with errors="replace" so a stray
# byte (e.g. the ✔ glyph some CLI versions emit) can't crash detection.
# stdout/stderr are left to render_next_steps, which wraps them UTF-8 at
# import — wrapping them a second time here would orphan an intermediate
# TextIOWrapper whose __del__ closes the underlying buffer.
if hasattr(sys.stdin, "buffer"):
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8",
                                 errors="replace")

# Sibling init-time modules. When run as `python configure_linear.py`, the
# script's own directory is sys.path[0], so these resolve directly. The
# detection / merge / render *logic* lives in them — this file is a thin
# orchestration layer (see scripts/README.md). Importing render_next_steps
# also forces UTF-8 on stdout/stderr (see note above).
import detect_linear_mcp_namespace as detect
import merge_settings_permissions as perms
import render_next_steps

_PLACEHOLDER = "REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE"

_DETECTION_FAILED_NOTE = (
    "No Linear MCP server detected. Substitute "
    "<REPLACE_WITH_YOUR_LINEAR_MCP_NAMESPACE> below with your actual "
    "namespace (see README \"Linear MCP tools\" for the three variants in "
    "the wild), then add each line to your .claude/settings.local.json "
    "permissions.allow array."
)


def _detect_namespace(stdin_text, mcp_json_path):
    """Return (namespace_or_None, extras). stdin first, .mcp.json fallback.

    Mirrors detect_linear_mcp_namespace.main()'s stdin-then-JSON order, but
    returns instead of exiting so the orchestrator can stay on the
    best-effort path. A malformed `.mcp.json` is treated as "no hit" rather
    than a hard failure (detect's scanner exits 1 there; we swallow it)."""
    first, extras = detect._scan_mcp_list(stdin_text)
    if first is not None:
        return first, extras
    if mcp_json_path:
        path = Path(mcp_json_path)
        if path.is_file():
            try:
                return detect._scan_mcp_json(path)
            except SystemExit:
                return None, []
    return None, []


def _write_local_settings(settings_path, namespace):
    """Merge the allowlist into settings.local.json. Returns True on write.

    Reuses merge_settings_permissions's merge logic; the read/validate around
    it mirrors that script's main() but degrades to a stderr note + False
    (no write) instead of exiting, since this step is best-effort."""
    path = Path(settings_path)
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"Cadence: could not parse {path}: {e}\n"
                  "Leaving it untouched; add the allowlist below by hand.",
                  file=sys.stderr)
            return False
        if not isinstance(existing, dict):
            print(f"Cadence: {path} is not a JSON object. Leaving it "
                  "untouched; add the allowlist below by hand.",
                  file=sys.stderr)
            return False
    else:
        existing = {}

    merged = perms._merge_into_settings(existing, perms._generate_entries(namespace))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def configure(stdin_text, settings_local_path, mcp_json_path):
    """Detect, merge, and return the rendered "Next steps" block."""
    namespace, extras = _detect_namespace(stdin_text, mcp_json_path)

    if namespace is not None:
        if extras:
            print(
                "Cadence: multiple Linear MCP servers found "
                f"(picked {namespace}; also saw: {', '.join(extras)}). "
                "Adjust the namespace by hand if the wrong one was chosen.",
                file=sys.stderr,
            )
        settings_local_written = _write_local_settings(settings_local_path, namespace)
        detection_note = f"Detected Linear MCP namespace: {namespace}"
        block_namespace = namespace
    else:
        settings_local_written = False
        detection_note = _DETECTION_FAILED_NOTE
        block_namespace = _PLACEHOLDER

    permissions_block = "\n".join(perms._generate_entries(block_namespace))

    return render_next_steps.render(
        settings_local_written, detection_note, permissions_block
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugin-root", required=True,
                    help="Directory ${CLAUDE_PLUGIN_ROOT} resolved to "
                         "(kept for interface parity with the other init "
                         "scripts; this orchestrator reads no plugin files).")
    ap.add_argument("--settings-local-path", required=True,
                    help="Path to .claude/settings.local.json to merge into.")
    ap.add_argument("--mcp-json-path",
                    help="Fallback .mcp.json to scan when `claude mcp list` "
                         "stdin yields no Linear server.")
    args = ap.parse_args()

    stdin_text = sys.stdin.read() if not sys.stdin.isatty() else ""

    sys.stdout.write(configure(
        stdin_text, args.settings_local_path, args.mcp_json_path
    ))
    sys.exit(0)


if __name__ == "__main__":
    main()
