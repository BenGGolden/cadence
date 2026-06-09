"""Tests for templates/cadence/hooks/render_sweep_report.py.

Covers the classification (stale vs fresh) including the boundary case at
exactly the cutoff, the per-issue stale_minutes math, the dual-stream
contract (Markdown on stdout, classification JSON on stderr), the
"(none cleared)" / "(none)" empty-table substitutions, the ascending
updated_at ordering, title truncation, the threshold-0 and large-threshold
edge cases, and CLI error paths.

One byte-identical fixture comparison (the broad-stroke acceptance
criterion) guards against any future edit to the renderer that
changes the visible report shape.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "cadence" / "hooks" / "render_sweep_report.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sweep"


def _issue(identifier, *, updated_at, title="A title",
           state_name="Implementing"):
    return {
        "identifier": identifier,
        "title": title,
        "updated_at": updated_at,
        "state_name": state_name,
    }


def _payload(*, now="2026-05-28T12:00:00Z", threshold_minutes=30,
             locked_issues=None):
    return {
        "now": now,
        "threshold_minutes": threshold_minutes,
        "locked_issues": locked_issues or [],
    }


def _run(payload, td):
    input_path = td / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(input_path)],
        cwd=str(td), capture_output=True, text=True,
        encoding="utf-8",
    )


def _classification(stderr):
    """Parse the stderr JSON classification block."""
    return json.loads(stderr)


class FixtureByteIdentityTests(unittest.TestCase):

    def test_mixed_byte_identical(self):
        """2 stale + 1 fresh, both tables populated, sorted by
        updated_at ascending."""
        payload = _payload(locked_issues=[
            # Provide in non-sorted order to also assert the sort.
            _issue("ENG-102", title="Fresh enough",
                   updated_at="2026-05-28T11:55:00Z"),
            _issue("ENG-100", title="Oldest stale",
                   updated_at="2026-05-28T09:00:00Z"),
            _issue("ENG-101", title="Middling stale",
                   updated_at="2026-05-28T10:30:00Z"),
        ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            expected = (FIXTURES / "mixed.md").read_text(encoding="utf-8")
            self.assertEqual(r.stdout, expected)


class ClassificationTests(unittest.TestCase):

    def test_empty_locked_list_renders_none_in_both_tables(self):
        payload = _payload(locked_issues=[])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("(none cleared)", r.stdout)
            self.assertIn("(none)", r.stdout)
            self.assertIn("Locked issues found: **0**", r.stdout)
            # Neither table header should appear.
            self.assertNotIn("| Identifier | Title | Last activity | Stale",
                             r.stdout)
            classification = _classification(r.stderr)
            self.assertEqual(classification["stale"], [])
            self.assertEqual(classification["fresh"], [])

    def test_all_stale_populates_cleared_only(self):
        payload = _payload(locked_issues=[
            _issue("ENG-1", updated_at="2026-05-28T10:00:00Z"),
            _issue("ENG-2", updated_at="2026-05-28T09:00:00Z"),
        ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("### Cleared", r.stdout)
            self.assertIn("| ENG-1 | A title |", r.stdout)
            self.assertIn("(none)", r.stdout)  # fresh table empty
            classification = _classification(r.stderr)
            self.assertEqual(len(classification["stale"]), 2)
            self.assertEqual(classification["fresh"], [])

    def test_all_fresh_populates_still_locked_only(self):
        payload = _payload(locked_issues=[
            _issue("ENG-1", updated_at="2026-05-28T11:55:00Z"),
            _issue("ENG-2", updated_at="2026-05-28T11:50:00Z"),
        ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("(none cleared)", r.stdout)
            self.assertIn("### Still locked", r.stdout)
            self.assertIn("| ENG-1 | A title |", r.stdout)
            classification = _classification(r.stderr)
            self.assertEqual(classification["stale"], [])
            self.assertEqual(len(classification["fresh"]), 2)

    def test_boundary_issue_at_cutoff_is_stale(self):
        """sweep.md step 4: `updatedAt <= cutoff` → stale. Exact equality
        belongs in the stale bucket."""
        payload = _payload(now="2026-05-28T12:00:00Z",
                           threshold_minutes=30,
                           locked_issues=[
                               _issue("ENG-1",
                                      updated_at="2026-05-28T11:30:00Z"),
                           ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            classification = _classification(r.stderr)
            self.assertEqual(len(classification["stale"]), 1)
            self.assertEqual(classification["stale"][0]["stale_minutes"], 30)

    def test_stale_minutes_floor(self):
        """89.something minutes → floor to 89."""
        payload = _payload(now="2026-05-28T12:00:00Z",
                           threshold_minutes=30,
                           locked_issues=[
                               _issue("ENG-1",
                                      updated_at="2026-05-28T10:30:30Z"),
                           ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            classification = _classification(r.stderr)
            self.assertEqual(classification["stale"][0]["stale_minutes"], 89)

    def test_future_updated_at_clamps_stale_minutes_to_zero(self):
        """Clock skew between MCP and the bootstrap can produce an
        updated_at slightly after `now`. The renderer must not emit a
        negative integer."""
        payload = _payload(now="2026-05-28T12:00:00Z",
                           threshold_minutes=30,
                           locked_issues=[
                               _issue("ENG-1",
                                      updated_at="2026-05-28T12:05:00Z"),
                           ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            classification = _classification(r.stderr)
            self.assertEqual(len(classification["fresh"]), 1)
            self.assertEqual(classification["fresh"][0]["stale_minutes"], 0)


class SortOrderTests(unittest.TestCase):

    def test_stale_sorted_by_updated_at_ascending(self):
        payload = _payload(locked_issues=[
            _issue("ENG-NEW", updated_at="2026-05-28T11:00:00Z"),
            _issue("ENG-OLD", updated_at="2026-05-28T08:00:00Z"),
            _issue("ENG-MID", updated_at="2026-05-28T09:30:00Z"),
        ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            old_idx = r.stdout.index("ENG-OLD")
            mid_idx = r.stdout.index("ENG-MID")
            new_idx = r.stdout.index("ENG-NEW")
            self.assertLess(old_idx, mid_idx)
            self.assertLess(mid_idx, new_idx)

    def test_fresh_sorted_by_updated_at_ascending(self):
        payload = _payload(locked_issues=[
            _issue("ENG-NEW", updated_at="2026-05-28T11:58:00Z"),
            _issue("ENG-OLDISH", updated_at="2026-05-28T11:32:00Z"),
            _issue("ENG-MID", updated_at="2026-05-28T11:45:00Z"),
        ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            oldish_idx = r.stdout.index("ENG-OLDISH")
            mid_idx = r.stdout.index("ENG-MID")
            new_idx = r.stdout.index("ENG-NEW")
            self.assertLess(oldish_idx, mid_idx)
            self.assertLess(mid_idx, new_idx)


class TitleTruncationTests(unittest.TestCase):

    def test_long_title_truncated_to_60_chars_with_ellipsis(self):
        long_title = "A" * 100
        payload = _payload(locked_issues=[
            _issue("ENG-1", title=long_title,
                   updated_at="2026-05-28T10:00:00Z"),
        ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("…", r.stdout)
            self.assertIn("A" * 59 + "…", r.stdout)
            self.assertNotIn("A" * 100, r.stdout)

    def test_newline_in_title_collapses_to_space(self):
        payload = _payload(locked_issues=[
            _issue("ENG-1", title="First line\nSecond line",
                   updated_at="2026-05-28T10:00:00Z"),
        ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("First line Second line", r.stdout)

    def test_pipe_in_title_is_escaped(self):
        payload = _payload(locked_issues=[
            _issue("ENG-1", title="Has | a pipe",
                   updated_at="2026-05-28T10:00:00Z"),
        ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("Has \\| a pipe", r.stdout)


class ThresholdEdgeCaseTests(unittest.TestCase):

    def test_threshold_zero_all_stale(self):
        """Cutoff = now, so every issue with updated_at <= now is
        stale."""
        payload = _payload(threshold_minutes=0,
                           locked_issues=[
                               _issue("ENG-1",
                                      updated_at="2026-05-28T11:55:00Z"),
                               _issue("ENG-2",
                                      updated_at="2026-05-28T08:00:00Z"),
                           ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            classification = _classification(r.stderr)
            self.assertEqual(len(classification["stale"]), 2)
            self.assertEqual(classification["fresh"], [])
            self.assertIn("Threshold: **0** minutes", r.stdout)

    def test_threshold_huge_none_stale(self):
        """Threshold 99999 → cutoff far in the past → nothing stale."""
        payload = _payload(threshold_minutes=99999,
                           locked_issues=[
                               _issue("ENG-1",
                                      updated_at="2026-05-28T11:55:00Z"),
                               _issue("ENG-2",
                                      updated_at="2026-05-28T08:00:00Z"),
                           ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            classification = _classification(r.stderr)
            self.assertEqual(classification["stale"], [])
            self.assertEqual(len(classification["fresh"]), 2)
            self.assertIn("Threshold: **99999** minutes", r.stdout)


class TimestampParsingTests(unittest.TestCase):

    def test_fractional_seconds_in_updated_at_parsed(self):
        """Linear typically returns timestamps like
        2026-05-28T11:00:00.000Z — the renderer must accept them."""
        payload = _payload(now="2026-05-28T12:00:00Z",
                           threshold_minutes=30,
                           locked_issues=[
                               _issue("ENG-1",
                                      updated_at="2026-05-28T11:00:00.123Z"),
                           ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            classification = _classification(r.stderr)
            self.assertEqual(len(classification["stale"]), 1)
            self.assertEqual(classification["stale"][0]["stale_minutes"], 59)

    def test_non_utc_offset_in_updated_at_parsed(self):
        """A Linear timestamp with a non-UTC offset still parses; the
        renderer compares against `now` after normalising to UTC."""
        payload = _payload(now="2026-05-28T12:00:00Z",
                           threshold_minutes=30,
                           locked_issues=[
                               # 11:00 UTC = 13:00+02:00.
                               _issue("ENG-1",
                                      updated_at="2026-05-28T13:00:00+02:00"),
                           ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            classification = _classification(r.stderr)
            self.assertEqual(len(classification["stale"]), 1)
            self.assertEqual(classification["stale"][0]["stale_minutes"], 60)


class HeaderCutoffTests(unittest.TestCase):

    def test_cutoff_computed_from_threshold(self):
        payload = _payload(now="2026-05-28T12:00:00Z",
                           threshold_minutes=45,
                           locked_issues=[])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("cutoff 2026-05-28T11:15:00Z", r.stdout)

    def test_cutoff_in_stderr_classification(self):
        payload = _payload(now="2026-05-28T12:00:00Z",
                           threshold_minutes=30,
                           locked_issues=[])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            classification = _classification(r.stderr)
            self.assertEqual(classification["cutoff"],
                             "2026-05-28T11:30:00Z")


class CliErrorTests(unittest.TestCase):

    def test_missing_input_arg_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=td, capture_output=True, text=True,
                encoding="utf-8",
            )
            self.assertNotEqual(r.returncode, 0)

    def test_unreadable_input_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            r = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--input", str(td / "missing.json")],
                cwd=str(td), capture_output=True, text=True,
                encoding="utf-8",
            )
            self.assertEqual(r.returncode, 1)

    def test_non_object_input_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            p = td / "input.json"
            p.write_text("[1, 2, 3]", encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(p)],
                cwd=str(td), capture_output=True, text=True,
                encoding="utf-8",
            )
            self.assertEqual(r.returncode, 1)

    def test_missing_now_exits_1(self):
        payload = {"threshold_minutes": 30, "locked_issues": []}
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 1)
            self.assertIn("now", r.stderr.lower())

    def test_non_integer_threshold_exits_1(self):
        payload = {"now": "2026-05-28T12:00:00Z",
                   "threshold_minutes": "30", "locked_issues": []}
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 1)
            self.assertIn("threshold_minutes", r.stderr)

    def test_negative_threshold_exits_1(self):
        payload = _payload(threshold_minutes=-1, locked_issues=[])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 1)

    def test_locked_issue_missing_identifier_exits_1(self):
        payload = {"now": "2026-05-28T12:00:00Z",
                   "threshold_minutes": 30,
                   "locked_issues": [{"updated_at": "2026-05-28T10:00:00Z"}]}
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 1)
            self.assertIn("identifier", r.stderr)

    def test_locked_issue_missing_updated_at_exits_1(self):
        payload = {"now": "2026-05-28T12:00:00Z",
                   "threshold_minutes": 30,
                   "locked_issues": [{"identifier": "ENG-1"}]}
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 1)
            self.assertIn("updated_at", r.stderr)

    def test_unparseable_updated_at_exits_1(self):
        payload = _payload(locked_issues=[
            {"identifier": "ENG-1", "title": "x",
             "updated_at": "not an ISO date"}
        ])
        with tempfile.TemporaryDirectory() as td:
            r = _run(payload, Path(td))
            self.assertEqual(r.returncode, 1)
            self.assertIn("ISO 8601", r.stderr)


if __name__ == "__main__":
    unittest.main()
