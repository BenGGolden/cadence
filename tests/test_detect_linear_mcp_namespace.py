"""Tests for scripts/detect_linear_mcp_namespace.py.

Covers the three known namespaces (`linear`, `linear-server`,
`claude_ai_Linear`), the multi-match warning, the empty-input → exit 2
path, and the `.mcp.json` fallback when the CLI output is empty.

The script is invoked via subprocess so the full argparse + stdin path
runs end-to-end.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "detect_linear_mcp_namespace.py"


def _run_stdin(stdin_text, *, json_path=None):
    args = [sys.executable, str(SCRIPT), "--mcp-list-stdin"]
    if json_path is not None:
        args += ["--mcp-json-path", str(json_path)]
    return subprocess.run(
        args, input=stdin_text, capture_output=True, text=True,
        encoding="utf-8",
    )


def _run_json_only(json_path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--mcp-json-path", str(json_path)],
        capture_output=True, text=True, encoding="utf-8",
    )


class McpListDetectionTests(unittest.TestCase):
    """Detection from `claude mcp list` output piped on stdin."""

    def test_detects_linear_official_name(self):
        # The official Linear MCP installed under the name `linear`.
        out = _run_stdin("linear: ✔ connected\n")
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "linear")

    def test_detects_linear_server_name(self):
        # Common Windows install name from Linear's own docs.
        out = _run_stdin("linear-server: ✔ connected\n")
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "linear-server")

    def test_detects_claude_ai_workspace_connector(self):
        out = _run_stdin("claude_ai_Linear: ✔ connected\n")
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "claude_ai_Linear")

    def test_detects_with_bullet_prefix(self):
        # Some CLI versions render the list as bulleted entries.
        out = _run_stdin("* linear-server: connected\n")
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "linear-server")

    def test_detects_with_dash_prefix(self):
        out = _run_stdin("- linear: connected\n")
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "linear")

    def test_skips_non_linear_servers(self):
        stdin = (
            "github: ✔ connected\n"
            "atlassian: ✔ connected\n"
            "linear-server: ✔ connected\n"
            "context7: ✔ connected\n"
        )
        out = _run_stdin(stdin)
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "linear-server")

    def test_first_match_wins_extras_reported_on_stderr(self):
        # Operator has two Linear MCP servers installed.
        stdin = (
            "linear: ✔ connected\n"
            "linear-server: ✔ connected\n"
        )
        out = _run_stdin(stdin)
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "linear")
        # Extras surfaced so the operator can adjust by hand if they want
        # the other one.
        self.assertIn("linear-server", out.stderr)
        self.assertIn("multiple Linear MCP servers", out.stderr)

    def test_empty_input_no_json_fallback_exits_2(self):
        out = _run_stdin("")
        self.assertEqual(out.returncode, 2, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "")

    def test_no_linear_match_exits_2(self):
        stdin = (
            "github: ✔ connected\n"
            "atlassian: ✔ connected\n"
        )
        out = _run_stdin(stdin)
        self.assertEqual(out.returncode, 2, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "")


class McpJsonFallbackTests(unittest.TestCase):
    """Detection from a .mcp.json fallback path."""

    def test_json_only_call_detects_linear_key(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            json_path = td_path / ".mcp.json"
            json_path.write_text(
                '{"mcpServers": {"linear-server": {"command": "npx"}}}\n',
                encoding="utf-8",
            )
            out = _run_json_only(json_path)
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "linear-server")

    def test_stdin_empty_falls_back_to_json(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            json_path = td_path / ".mcp.json"
            json_path.write_text(
                '{"mcpServers": {"linear": {"command": "npx"}}}\n',
                encoding="utf-8",
            )
            out = _run_stdin("", json_path=json_path)
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "linear")

    def test_stdin_match_short_circuits_json_fallback(self):
        # When stdin already yields a hit, the JSON path is not consulted —
        # so a bogus JSON file should still produce the stdin's result.
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            json_path = td_path / ".mcp.json"
            # Deliberately invalid JSON; if the script touched it we'd
            # see exit 1.
            json_path.write_text("not json at all", encoding="utf-8")
            out = _run_stdin("linear-server: connected\n", json_path=json_path)
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "linear-server")

    def test_missing_json_file_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            json_path = td_path / "absent.json"
            out = _run_stdin("", json_path=json_path)
        self.assertEqual(out.returncode, 2, msg=out.stderr)
        self.assertEqual(out.stdout.strip(), "")

    def test_unparseable_json_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            json_path = td_path / ".mcp.json"
            json_path.write_text("{ not valid", encoding="utf-8")
            out = _run_stdin("", json_path=json_path)
        # stdin empty → script falls through to JSON path, parse fails.
        self.assertEqual(out.returncode, 1, msg=out.stderr)
        self.assertIn("could not parse", out.stderr)

    def test_json_with_no_linear_server_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            json_path = td_path / ".mcp.json"
            json_path.write_text(
                '{"mcpServers": {"github": {}, "atlassian": {}}}\n',
                encoding="utf-8",
            )
            out = _run_stdin("", json_path=json_path)
        self.assertEqual(out.returncode, 2, msg=out.stderr)


class CliErrorTests(unittest.TestCase):
    def test_no_flags_exits_1(self):
        out = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(out.returncode, 1, msg=out.stderr)
        self.assertIn("--mcp-list-stdin", out.stderr)


if __name__ == "__main__":
    unittest.main()
