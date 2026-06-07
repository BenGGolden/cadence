"""Tests for scripts/scaffold_files.py.

Each test builds a fake plugin root (every SCAFFOLD_PLAN source materialised
with distinctive, non-ASCII-containing bytes) plus a fresh consumer working
directory, then invokes the script via subprocess with cwd set to the
consumer dir. Byte-identity is asserted with hashlib.sha256 against the fake
sources, so the "model patches instead of copying" regression (AC-4b) is
caught precisely.

The plan-integrity tests import SCAFFOLD_PLAN directly and assert the
category tagging and destination uniqueness invariants.
"""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "scaffold_files.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "init"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from scaffold_files import SCAFFOLD_PLAN  # noqa: E402

_REQUIRED_DIRS = (
    ".claude",
    ".claude/agents/cadence",
    ".claude/prompts",
    ".claude/cadence/hooks",
    ".claude/commands/cadence",
)


def _fake_source_bytes(src_rel):
    # Distinctive per-file content with a non-ASCII tail so byte-for-byte
    # preservation (no newline translation, no encoding mangling) is tested.
    return f"SOURCE {src_rel}\nsecond line — ñ ✔\n".encode("utf-8")


def _make_plugin_root(tmp):
    """Materialise every SCAFFOLD_PLAN source under `tmp`. Returns tmp."""
    root = Path(tmp)
    for src_rel, _, _ in SCAFFOLD_PLAN:
        src = root / src_rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(_fake_source_bytes(src_rel))
    return root


def _run(plugin_root, cwd, *, force=False):
    args = [sys.executable, str(SCRIPT), "--plugin-root", str(plugin_root)]
    if force:
        args.append("--force")
    return subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
    )


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class HappyPathTests(unittest.TestCase):
    def test_clean_tree_copies_every_destination_byte_identical(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            out = _run(root, c)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            consumer = Path(c)
            for src_rel, dest_rel, _ in SCAFFOLD_PLAN:
                dest = consumer / dest_rel
                self.assertTrue(dest.is_file(), f"missing {dest_rel}")
                self.assertEqual(_sha(dest), _sha(root / src_rel),
                                 f"content mismatch for {dest_rel}")
            for d in _REQUIRED_DIRS:
                self.assertTrue((consumer / d).is_dir(), f"missing dir {d}")
            expected = (FIXTURES / "scaffold_success_no_skips.txt").read_text(
                encoding="utf-8")
            self.assertEqual(out.stdout, expected)

    def test_idempotent_with_force(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            first = _run(root, c, force=True)
            state1 = {d: _sha(Path(c) / d) for _, d, _ in SCAFFOLD_PLAN}
            second = _run(root, c, force=True)
            state2 = {d: _sha(Path(c) / d) for _, d, _ in SCAFFOLD_PLAN}
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(state1, state2)


class AbortPathTests(unittest.TestCase):
    def test_workflow_yaml_present_no_force_aborts_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            consumer = Path(c)
            (consumer / ".claude").mkdir()
            sentinel = b"OPERATOR CONTENT\n"
            (consumer / ".claude/workflow.yaml").write_bytes(sentinel)
            out = _run(root, c)
            self.assertEqual(out.returncode, 2, msg=out.stderr)
            expected = (FIXTURES / "scaffold_abort.txt").read_text(
                encoding="utf-8")
            self.assertEqual(out.stdout, expected)
            # workflow.yaml untouched; no other destination or dir created.
            self.assertEqual((consumer / ".claude/workflow.yaml").read_bytes(),
                             sentinel)
            self.assertFalse((consumer / ".claude/cadence/hooks").exists())
            self.assertFalse((consumer / ".claude/agents/cadence/cadence-planner.md").exists())

    def test_workflow_yaml_present_with_force_copies_all(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            consumer = Path(c)
            (consumer / ".claude").mkdir()
            (consumer / ".claude/workflow.yaml").write_bytes(b"stale\n")
            out = _run(root, c, force=True)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            for src_rel, dest_rel, _ in SCAFFOLD_PLAN:
                self.assertEqual(_sha(consumer / dest_rel),
                                 _sha(root / src_rel), dest_rel)


class DefensiveSkipTests(unittest.TestCase):
    def test_user_config_present_no_force_is_skipped(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            consumer = Path(c)
            (consumer / ".claude/agents/cadence").mkdir(parents=True)
            preserved = b"MY PLANNER EDITS\n"
            (consumer / ".claude/agents/cadence/cadence-planner.md").write_bytes(preserved)
            out = _run(root, c)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertEqual(
                (consumer / ".claude/agents/cadence/cadence-planner.md").read_bytes(),
                preserved)
            # Every other destination copied.
            for src_rel, dest_rel, _ in SCAFFOLD_PLAN:
                if dest_rel == ".claude/agents/cadence/cadence-planner.md":
                    continue
                self.assertEqual(_sha(consumer / dest_rel),
                                 _sha(root / src_rel), dest_rel)
            expected = (FIXTURES / "scaffold_success_with_skip.txt").read_text(
                encoding="utf-8")
            self.assertEqual(out.stdout, expected)

    def test_user_config_present_with_force_is_overwritten(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            consumer = Path(c)
            (consumer / ".claude/agents/cadence").mkdir(parents=True)
            (consumer / ".claude/agents/cadence/cadence-planner.md").write_bytes(b"edits\n")
            out = _run(root, c, force=True)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertEqual(
                _sha(consumer / ".claude/agents/cadence/cadence-planner.md"),
                _sha(root / "templates/agents/cadence/cadence-planner.md"))


class PluginOwnedOverwriteTests(unittest.TestCase):
    def test_plugin_owned_overwritten_even_without_force(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            consumer = Path(c)
            (consumer / ".claude/cadence/hooks").mkdir(parents=True)
            (consumer / ".claude/cadence/hooks/validate_workflow.py").write_bytes(
                b"STALE HOOK\n")
            # workflow.yaml absent → step 2 does not abort; no --force.
            out = _run(root, c)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            self.assertEqual(
                _sha(consumer / ".claude/cadence/hooks/validate_workflow.py"),
                _sha(root / "templates/cadence/hooks/validate_workflow.py"))


class ReinitByteForByteTests(unittest.TestCase):
    """The actual bug this PR fixes (AC-4b)."""

    def _scramble_all(self, consumer):
        for i, (_, dest_rel, _) in enumerate(SCAFFOLD_PLAN):
            dest = consumer / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Mix of truncation and randomised-ish bytes.
            dest.write_bytes(b"" if i % 3 == 0 else f"SCRAMBLED{i}".encode())

    def test_force_reinit_makes_every_dest_byte_identical(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            consumer = Path(c)
            self._scramble_all(consumer)
            out = _run(root, c, force=True)
            self.assertEqual(out.returncode, 0, msg=out.stderr)
            for src_rel, dest_rel, _ in SCAFFOLD_PLAN:
                self.assertEqual(_sha(consumer / dest_rel),
                                 _sha(root / src_rel),
                                 f"{dest_rel} not byte-identical to source")

    def test_force_reinit_idempotent_twice(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            consumer = Path(c)
            self._scramble_all(consumer)
            first = _run(root, c, force=True)
            state1 = {d: _sha(consumer / d) for _, d, _ in SCAFFOLD_PLAN}
            second = _run(root, c, force=True)
            state2 = {d: _sha(consumer / d) for _, d, _ in SCAFFOLD_PLAN}
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(state1, state2)


class ErrorPathTests(unittest.TestCase):
    def test_missing_plugin_root_exits_1(self):
        with tempfile.TemporaryDirectory() as c:
            missing = Path(c) / "does-not-exist"
            out = _run(missing, c)
            self.assertEqual(out.returncode, 1, msg=out.stdout)
            # The first source under the missing root is named.
            self.assertIn("could not read source", out.stderr)

    def test_single_missing_source_exits_1_partial_ok(self):
        with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as c:
            root = _make_plugin_root(p)
            # Remove one source after building the rest.
            (root / "templates/cadence/hooks/render_sweep_report.py").unlink()
            out = _run(root, c)
            self.assertEqual(out.returncode, 1, msg=out.stdout)
            self.assertIn("render_sweep_report.py", out.stderr)
            # Files copied before the failure remain (acceptable partial).
            self.assertTrue((Path(c) / ".claude/workflow.yaml").is_file())

    def test_missing_required_arg_exits_2(self):
        out = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(out.returncode, 2)


class PlanIntegrityTests(unittest.TestCase):
    def test_every_policy_is_allowed(self):
        for _, dest, policy in SCAFFOLD_PLAN:
            self.assertIn(policy, ("plugin-owned", "user-config"), dest)

    def test_category_tagging(self):
        for src, dest, policy in SCAFFOLD_PLAN:
            if src.startswith("templates/cadence/hooks/") or src.startswith("commands/"):
                self.assertEqual(policy, "plugin-owned", src)
            elif (src.startswith("templates/agents/")
                  or src.startswith("templates/prompts/")):
                self.assertEqual(policy, "user-config", src)

    def test_destinations_are_unique(self):
        dests = [d for _, d, _ in SCAFFOLD_PLAN]
        self.assertEqual(len(dests), len(set(dests)))

    def test_destinations_match_render_next_steps_file_list(self):
        from render_next_steps import _FILES_WRITTEN
        self.assertEqual(tuple(d for _, d, _ in SCAFFOLD_PLAN), _FILES_WRITTEN)


if __name__ == "__main__":
    unittest.main()
