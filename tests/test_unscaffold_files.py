"""Tests for scripts/unscaffold_files.py.

Mirrors test_scaffold_files.py: materialise every SCAFFOLD_PLAN destination
(plus .cadence/ and a settings file) in a tempdir, run the removal driver via
subprocess with cwd set to the consumer dir, and assert filesystem state +
byte-identical stdout against golden fixtures under tests/fixtures/uninstall/.
"""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "unscaffold_files.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "uninstall"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from scaffold_files import SCAFFOLD_PLAN  # noqa: E402


def _materialize(root):
    """Create every SCAFFOLD_PLAN dest + .cadence/ (nested) + a settings file."""
    for _, dest_rel, _ in SCAFFOLD_PLAN:
        p = root / dest_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    cad = root / ".cadence"
    (cad / "sub").mkdir(parents=True, exist_ok=True)
    (cad / ".gitignore").write_text("*\n", encoding="utf-8")
    (cad / "sub" / "state.json").write_text("{}\n", encoding="utf-8")
    # A non-Cadence file that must keep .claude/ alive.
    (root / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")


def _run(cwd, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
    )


def _tree_hash(root):
    """A stable hash of the entire tree (relative paths + contents)."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _plugin_owned():
    return [d for _, d, pol in SCAFFOLD_PLAN if pol == "plugin-owned"]


def _user_config():
    return [d for _, d, pol in SCAFFOLD_PLAN if pol == "user-config"]


class NoForceTests(unittest.TestCase):
    def test_removes_plugin_owned_keeps_user_config(self):
        with tempfile.TemporaryDirectory() as c:
            root = Path(c)
            _materialize(root)
            out = _run(c)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            for d in _plugin_owned():
                self.assertFalse((root / d).exists(), f"{d} should be gone")
            for d in _user_config():
                self.assertTrue((root / d).exists(), f"{d} should remain")
            # .cadence/ gone; .claude/ survives (settings.json + user-config).
            self.assertFalse((root / ".cadence").exists())
            self.assertTrue((root / ".claude").is_dir())
            self.assertTrue((root / ".claude/settings.json").is_file())
            # Empty Cadence dirs pruned; user-config parents kept.
            self.assertFalse((root / ".claude/hooks").exists())
            self.assertFalse((root / ".claude/commands").exists())
            self.assertFalse((root / ".claude/worktrees").exists())
            self.assertTrue((root / ".claude/agents").is_dir())
            self.assertTrue((root / ".claude/prompts").is_dir())
            expected = (FIXTURES / "remove_no_force.txt").read_text(encoding="utf-8")
            self.assertEqual(out.stdout, expected)


class ForceTests(unittest.TestCase):
    def test_removes_user_config_and_their_dirs(self):
        with tempfile.TemporaryDirectory() as c:
            root = Path(c)
            _materialize(root)
            out = _run(c, "--force")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            for _, d, _ in SCAFFOLD_PLAN:
                self.assertFalse((root / d).exists(), f"{d} should be gone")
            self.assertFalse((root / ".claude/agents").exists())
            self.assertFalse((root / ".claude/prompts").exists())
            # .claude/ itself survives (settings.json still there).
            self.assertTrue((root / ".claude").is_dir())
            expected = (FIXTURES / "remove_force.txt").read_text(encoding="utf-8")
            self.assertEqual(out.stdout, expected)


class DryRunTests(unittest.TestCase):
    def test_writes_nothing_and_previews(self):
        with tempfile.TemporaryDirectory() as c:
            root = Path(c)
            _materialize(root)
            before = _tree_hash(root)
            out = _run(c, "--dry-run")
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertEqual(_tree_hash(root), before, "tree changed on dry-run")
            expected = (FIXTURES / "dry_run.txt").read_text(encoding="utf-8")
            self.assertEqual(out.stdout, expected)


class IdempotentTests(unittest.TestCase):
    def test_second_run_reports_already_absent(self):
        with tempfile.TemporaryDirectory() as c:
            root = Path(c)
            _materialize(root)
            first = _run(c, "--force")
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            after_first = _tree_hash(root)
            second = _run(c, "--force")
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            self.assertEqual(_tree_hash(root), after_first, "second run mutated tree")
            self.assertIn("already absent", second.stdout)
            self.assertIn(".cadence/ already absent", second.stdout)


class CadenceRecursiveTests(unittest.TestCase):
    def test_nested_cadence_removed(self):
        with tempfile.TemporaryDirectory() as c:
            root = Path(c)
            _materialize(root)
            self.assertTrue((root / ".cadence/sub/state.json").is_file())
            out = _run(c)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertFalse((root / ".cadence").exists())


class PlanIntegrityTests(unittest.TestCase):
    def test_removes_exactly_scaffold_plan_dests(self):
        """No hard-coded second file list — removal is driven by SCAFFOLD_PLAN."""
        with tempfile.TemporaryDirectory() as c:
            root = Path(c)
            _materialize(root)
            _run(c, "--force")
            for _, d, _ in SCAFFOLD_PLAN:
                self.assertFalse((root / d).exists(), d)
            # A file outside the plan is never touched.
            self.assertTrue((root / ".claude/settings.json").is_file())


if __name__ == "__main__":
    unittest.main()
