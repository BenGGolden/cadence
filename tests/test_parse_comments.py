"""Tests for templates/hooks/parse_comments.py.

Comments arrive as a JSON file via --input. The script exits 0 in every
case; errors land in the `parse_errors` field of stdout JSON. Tests are
invoked via subprocess so the argparse + I/O paths are exercised.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "hooks" / "parse_comments.py"


def write_comments(tmpdir, comments):
    p = Path(tmpdir) / "comments.json"
    p.write_text(json.dumps(comments), encoding="utf-8")
    return p


def run_parser(input_path, target_state="implement", gate_name=None):
    args = [sys.executable, str(SCRIPT),
            "--input", str(input_path),
            "--target-state", target_state]
    if gate_name is not None:
        args += ["--gate-name", gate_name]
    return subprocess.run(args, capture_output=True, text=True)


def _comment(body, created_at, user="Alice", is_bot=False):
    return {
        "id": f"c-{created_at}",
        "body": body,
        "createdAt": created_at,
        "user": {"displayName": user, "isBot": is_bot},
    }


def attempt_marker(state, attempt, t, user="Alice"):
    payload = json.dumps({"state": state, "attempt": attempt, "started_at": t})
    body = (f"<!-- cadence:state {payload} -->\n"
            f"**[Cadence]** Entering state: **{state}** (attempt {attempt})")
    return _comment(body, t, user=user)


def legacy_attempt_marker(state, run, t, user="Alice"):
    payload = json.dumps({"state": state, "run": run, "timestamp": t})
    body = (f"<!-- stokowski:state {payload} -->\n"
            f"**[Stokowski]** Entering state: **{state}** (run {run})")
    return _comment(body, t, user=user)


def gate_rework(state, t, rework_to="implement", user="Alice"):
    payload = json.dumps({"state": state, "status": "rework",
                          "rework_to": rework_to})
    body = (f"<!-- cadence:gate {payload} -->\n"
            f"**[Cadence]** Rework requested; routing to **{rework_to}**.")
    return _comment(body, t, user=user)


def pr_comment(url, branch, t, user="Alice", is_bot=False):
    body = (f"Created PR: {url}\n"
            f"**Branch:** `{branch}`\n")
    return _comment(body, t, user=user, is_bot=is_bot)


class ParseCommentsTests(unittest.TestCase):

    def test_empty_list(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, [])
            r = run_parser(p)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["attempt_count"], 0)
            self.assertEqual(payload["rework_count"], 0)
            self.assertEqual(payload["rework_context"], [])
            self.assertEqual(payload["parse_errors"], [])
            self.assertEqual(payload["latest_implementer_summary"],
                             {"pr_url": None, "branch": None})
            self.assertEqual(payload["latest_tracking_comment"]["kind"], None)

    # ---------- attempt_count ----------

    def test_attempt_count_only_counts_target_state(self):
        comments = [
            attempt_marker("plan", 1, "2026-05-26T09:00:00Z"),
            attempt_marker("implement", 1, "2026-05-26T10:00:00Z"),
            attempt_marker("implement", 2, "2026-05-26T11:00:00Z"),
            attempt_marker("implement", 3, "2026-05-26T12:00:00Z"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(run_parser(p, target_state="implement").stdout)
            self.assertEqual(payload["attempt_count"], 3)

    def test_attempt_count_ignores_failure_records(self):
        # A failure record has a "status" key; only entry markers count.
        failure_payload = json.dumps({
            "state": "implement", "attempt": 1,
            "status": "failed", "error": "boom",
        })
        failure_body = (
            f"<!-- cadence:state {failure_payload} -->\n"
            "**[Cadence]** Subagent failed."
        )
        comments = [
            attempt_marker("implement", 1, "2026-05-26T09:00:00Z"),
            _comment(failure_body, "2026-05-26T09:30:00Z"),
            attempt_marker("implement", 2, "2026-05-26T10:00:00Z"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(run_parser(p, target_state="implement").stdout)
            self.assertEqual(payload["attempt_count"], 2)

    # ---------- rework_count ----------

    def test_rework_count_scoped_to_gate_name(self):
        comments = [
            gate_rework("human_review", "2026-05-26T09:00:00Z"),
            gate_rework("human_review", "2026-05-26T10:00:00Z"),
            gate_rework("plan_review", "2026-05-26T11:00:00Z"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(
                run_parser(p, gate_name="human_review").stdout)
            self.assertEqual(payload["rework_count"], 2)

    def test_rework_count_zero_without_gate_name(self):
        comments = [gate_rework("human_review", "2026-05-26T09:00:00Z")]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(run_parser(p).stdout)
            self.assertEqual(payload["rework_count"], 0)

    # ---------- rework_context ----------

    def test_rework_context_oldest_first_human_only(self):
        sweep_body = "<!-- cadence:sweep {\"cleared_at\":\"x\"} -->\nsweep"
        comments = [
            attempt_marker("implement", 1, "2026-05-26T09:00:00Z"),
            _comment("first human note", "2026-05-26T10:00:00Z", user="Bob"),
            _comment("bot output", "2026-05-26T10:30:00Z",
                     user="GitHub", is_bot=True),
            _comment(sweep_body, "2026-05-26T11:00:00Z", user="Cadence"),
            _comment("second human note", "2026-05-26T12:00:00Z", user="Carol"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(run_parser(p).stdout)
            bodies = [c["body"] for c in payload["rework_context"]]
            self.assertEqual(bodies, ["first human note", "second human note"])
            # Oldest-first means the bob comment precedes the carol comment.
            authors = [c["author"] for c in payload["rework_context"]]
            self.assertEqual(authors, ["Bob", "Carol"])

    def test_rework_context_empty_when_no_tracking_boundary(self):
        comments = [
            _comment("loose human comment", "2026-05-26T10:00:00Z", user="Bob"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(run_parser(p).stdout)
            self.assertEqual(payload["rework_context"], [])

    # ---------- latest_implementer_summary ----------

    def test_implementer_summary_extracted(self):
        comments = [
            attempt_marker("implement", 1, "2026-05-26T09:00:00Z", user="Alice"),
            pr_comment("https://github.com/o/r/pull/42", "feat/foo",
                       "2026-05-26T09:00:01Z", user="Alice"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(run_parser(p).stdout)
            self.assertEqual(
                payload["latest_implementer_summary"],
                {"pr_url": "https://github.com/o/r/pull/42",
                 "branch": "feat/foo"},
            )

    def test_implementer_summary_requires_matching_author(self):
        # If the attempt marker and the PR-bearing comment are from different
        # authors, the script must NOT claim the PR as the implementer's
        # summary. Removing the author-match constraint at parse_comments.py
        # line 197 breaks this (AC-4).
        comments = [
            attempt_marker("implement", 1, "2026-05-26T09:00:00Z", user="Alice"),
            pr_comment("https://github.com/o/r/pull/42", "feat/foo",
                       "2026-05-26T09:00:01Z", user="Bob"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(run_parser(p).stdout)
            self.assertEqual(
                payload["latest_implementer_summary"],
                {"pr_url": None, "branch": None},
            )

    def test_implementer_summary_picks_newest(self):
        comments = [
            attempt_marker("implement", 1, "2026-05-26T09:00:00Z", user="Alice"),
            pr_comment("https://github.com/o/r/pull/41", "feat/old",
                       "2026-05-26T09:00:01Z", user="Alice"),
            attempt_marker("implement", 2, "2026-05-26T10:00:00Z", user="Alice"),
            pr_comment("https://github.com/o/r/pull/99", "feat/new",
                       "2026-05-26T10:00:01Z", user="Alice"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(run_parser(p).stdout)
            self.assertEqual(
                payload["latest_implementer_summary"],
                {"pr_url": "https://github.com/o/r/pull/99",
                 "branch": "feat/new"},
            )

    # ---------- legacy stokowski normalisation ----------

    def test_stokowski_run_normalised_to_attempt(self):
        comments = [legacy_attempt_marker("implement", 2,
                                          "2026-05-26T09:00:00Z")]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            payload = json.loads(run_parser(p).stdout)
            self.assertEqual(payload["attempt_count"], 1)
            ltc = payload["latest_tracking_comment"]
            self.assertEqual(ltc["state"], "implement")
            self.assertEqual(ltc["attempt"], 2)
            self.assertEqual(ltc["raw_json"]["started_at"],
                             "2026-05-26T09:00:00Z")

    # ---------- malformed input ----------

    def test_malformed_tracking_json_surfaces_in_parse_errors(self):
        # The body is recognised as a tracking comment by prefix but the
        # embedded JSON cannot be parsed.
        comments = [_comment("<!-- cadence:state { not json -->\nbroken",
                             "2026-05-26T09:00:00Z")]
        with tempfile.TemporaryDirectory() as td:
            p = write_comments(td, comments)
            r = run_parser(p)
            self.assertEqual(r.returncode, 0)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["attempt_count"], 0)
            self.assertGreaterEqual(len(payload["parse_errors"]), 1)

    def test_non_array_input_surfaces_in_parse_errors(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "comments.json"
            p.write_text(json.dumps("not a list"), encoding="utf-8")
            r = run_parser(p)
            self.assertEqual(r.returncode, 0)
            payload = json.loads(r.stdout)
            self.assertGreaterEqual(len(payload["parse_errors"]), 1)


if __name__ == "__main__":
    unittest.main()
