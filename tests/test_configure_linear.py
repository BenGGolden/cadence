"""Tests for scripts/configure_linear.py.

configure_linear is the orchestrator that folds init.md Steps 4c + 5 into
one process: detect the Linear MCP namespace (stdin `claude mcp list`, then
`.mcp.json` fallback), merge the allowlist into .claude/settings.local.json
on a hit, and render the operator "Next steps" block on stdout.

The four branches today's 4c prose enumerates each assert the rendered
stdout against a byte-identical fixture. The detected branch (namespace
`linear`, settings.local written) renders exactly tests/fixtures/init/
next_steps_success.md; the detection-failed branch renders exactly
next_steps_failure.md — so those goldens double as configure_linear's
contract, guaranteeing the orchestrator and the renderer stay in lockstep.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "configure_linear.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "init"

_SUCCESS = (FIXTURES / "next_steps_success.md").read_text(encoding="utf-8")
_FAILURE = (FIXTURES / "next_steps_failure.md").read_text(encoding="utf-8")


def _run(stdin_text, cwd, *, settings_local="settings.local.json",
         mcp_json=None):
    args = [
        sys.executable, str(SCRIPT),
        "--plugin-root", str(REPO_ROOT),
        "--settings-local-path", settings_local,
    ]
    if mcp_json is not None:
        args += ["--mcp-json-path", mcp_json]
    return subprocess.run(
        args, input=stdin_text, cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8",
    )


class DetectedViaStdinTests(unittest.TestCase):
    def test_stdout_matches_success_fixture(self):
        with tempfile.TemporaryDirectory() as c:
            out = _run("linear: ✔ connected\n", c)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertEqual(out.stdout, _SUCCESS)

    def test_writes_settings_local_with_namespace_entries(self):
        with tempfile.TemporaryDirectory() as c:
            out = _run("linear: connected\n", c)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            data = json.loads(
                (Path(c) / "settings.local.json").read_text(encoding="utf-8"))
            allow = data["permissions"]["allow"]
            self.assertIn("mcp__linear__list_issues", allow)
            self.assertIn("mcp__linear__remove_label", allow)


class DetectedViaJsonFallbackTests(unittest.TestCase):
    def test_empty_stdin_falls_back_to_mcp_json(self):
        with tempfile.TemporaryDirectory() as c:
            (Path(c) / ".mcp.json").write_text(
                '{"mcpServers": {"linear": {"command": "npx"}}}\n',
                encoding="utf-8")
            out = _run("", c, mcp_json=".mcp.json")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertEqual(out.stdout, _SUCCESS)


class MultipleServersTests(unittest.TestCase):
    def test_first_match_wins_extras_on_stderr(self):
        with tempfile.TemporaryDirectory() as c:
            out = _run("linear: connected\nlinear-server: connected\n", c)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            # Picks the first; stdout is the `linear` success block.
            self.assertEqual(out.stdout, _SUCCESS)
            self.assertIn("multiple Linear MCP servers", out.stderr)
            self.assertIn("linear-server", out.stderr)


class DetectionFailedTests(unittest.TestCase):
    def test_no_server_renders_placeholder_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as c:
            out = _run("github: connected\n", c, mcp_json=".mcp.json")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertEqual(out.stdout, _FAILURE)
            self.assertFalse((Path(c) / "settings.local.json").exists())

    def test_missing_mcp_json_still_renders_placeholder(self):
        with tempfile.TemporaryDirectory() as c:
            # No stdin hit, .mcp.json absent → detection fails gracefully.
            out = _run("", c, mcp_json=".mcp.json")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertEqual(out.stdout, _FAILURE)


class MalformedSettingsTests(unittest.TestCase):
    def test_unparseable_settings_local_degrades_without_crashing(self):
        with tempfile.TemporaryDirectory() as c:
            (Path(c) / "settings.local.json").write_text(
                "{ not json", encoding="utf-8")
            out = _run("linear: connected\n", c)
            # Detection succeeded but the write is skipped; still exits 0
            # and still renders a block (settings.local line omitted).
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertIn("could not parse", out.stderr)
            self.assertNotIn(
                "  .claude/settings.local.json (Linear MCP allowlist merged in)",
                out.stdout)


if __name__ == "__main__":
    unittest.main()
