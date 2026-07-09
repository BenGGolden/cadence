"""Integration tests for templates/cadence/hooks/extract_findings.py.

Invoked via subprocess (matching test_parse_comments.py /
test_promote_acceptance_criteria.py) so the full argparse + I/O +
`import parse_comments` path runs on every case. The helper exits 0 in every
case; errors land in the `parse_errors` field of the stdout JSON.

These cases pin the marker→output pairing (plan / implement / agent_review,
latest-per-state, warning-tolerant), the structured reviewer-findings parse
(severity, [follow-up], location, recommendation banner), the empty-review and
missing-source shapes, and prior-triage detection.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "cadence" / "hooks" / "extract_findings.py"


def _comment(body, t, user="Cadence"):
    return {"id": f"c-{t}", "body": body, "createdAt": t,
            "user": {"displayName": user}}


def attempt_marker(state, attempt, t, user="Cadence"):
    """A `cadence:state` entry marker (no `status` key)."""
    payload = json.dumps({"state": state, "attempt": attempt, "started_at": t})
    body = (f"<!-- cadence:state {payload} -->\n"
            f"**[Cadence]** Entering state: **{state}** (attempt {attempt})")
    return _comment(body, t, user=user)


def warning_comment(t, parent="ENG-10", chars=9481, user="Cadence"):
    payload = json.dumps({"parent": parent, "chars": chars})
    body = (f"<!-- cadence:warning {payload} -->\n"
            f"**[Cadence]** Parent context for {parent} is large.")
    return _comment(body, t, user=user)


def output(body, t, user="Cadence"):
    """A paired subagent output (a plain, non-cadence comment)."""
    return _comment(body, t, user=user)


REVIEW_BODY = (
    "## Review\n\n"
    "**Recommendation: APPROVE** — 0 blocking, 2 major, 1 minor.\n\n"
    "**PR:** https://github.com/o/r/pull/9\n"
    "**Plan compliance:** On plan — matches the ticket.\n\n"
    "### Findings\n\n"
    "- **[major]** `src/api.ts:42` — no error handling on the 404 path.\n"
    "- **minor** [follow-up] `db/schema.sql:12` — index deferred to a later "
    "ticket.\n\n"
    "### Summary\n\n"
    "Approve; the two majors are worth a look.\n"
)


def _run(comments):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "comments.json"
        p.write_text(json.dumps(comments), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(p)],
            capture_output=True, text=True, encoding="utf-8")
        return r


class ExtractFindingsTests(unittest.TestCase):

    # ---------- reviewer findings parsed ----------

    def test_reviewer_findings_parsed(self):
        comments = [
            attempt_marker("agent_review", 1, "2026-05-01T10:00:00Z"),
            output(REVIEW_BODY, "2026-05-01T10:00:01Z"),
        ]
        r = _run(comments)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = json.loads(r.stdout)
        rev = out["reviewer"]
        self.assertTrue(rev["present"])
        self.assertEqual(rev["recommendation"],
                         "APPROVE — 0 blocking, 2 major, 1 minor.")
        self.assertEqual(len(rev["findings"]), 2)

        major = rev["findings"][0]
        self.assertEqual(major["severity"], "major")
        self.assertFalse(major["follow_up"])
        self.assertEqual(major["location"], "src/api.ts:42")

        follow = rev["findings"][1]
        self.assertEqual(follow["severity"], "minor")
        self.assertTrue(follow["follow_up"])
        self.assertEqual(follow["location"], "db/schema.sql:12")
        self.assertIn("index deferred", follow["text"])

    def test_reviewer_findings_tolerate_bracketless_severity(self):
        # The reviewer prompt is a user-config file an LLM may mirror either
        # way: a bracket-less `**major**` must parse exactly like `**[major]**`.
        body = ("## Review\n\n"
                "**Recommendation: REQUEST CHANGES** — 1 blocking, 0 major, "
                "0 minor.\n\n### Findings\n\n"
                "- **blocking** `x.py:1` — boom.\n")
        comments = [
            attempt_marker("agent_review", 1, "2026-05-01T10:00:00Z"),
            output(body, "2026-05-01T10:00:01Z"),
        ]
        out = json.loads(_run(comments).stdout)
        findings = out["reviewer"]["findings"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "blocking")
        self.assertFalse(findings[0]["follow_up"])
        self.assertEqual(out["reviewer"]["recommendation"],
                         "REQUEST CHANGES — 1 blocking, 0 major, 0 minor.")

    # ---------- three-source pairing ----------

    def test_three_source_pairing_latest_per_state(self):
        comments = [
            attempt_marker("plan", 1, "2026-05-01T09:00:00Z"),
            output("## Plan\n\nFirst plan.", "2026-05-01T09:00:01Z"),
            attempt_marker("implement", 1, "2026-05-01T10:00:00Z"),
            output("## Implementation\n\nOld attempt.", "2026-05-01T10:00:01Z"),
            # rework round: newer implement marker + output supersedes.
            attempt_marker("implement", 2, "2026-05-01T11:00:00Z"),
            output("## Implementation\n\nReworked attempt.",
                   "2026-05-01T11:00:01Z"),
            attempt_marker("agent_review", 1, "2026-05-01T12:00:00Z"),
            output(REVIEW_BODY, "2026-05-01T12:00:01Z"),
        ]
        out = json.loads(_run(comments).stdout)
        self.assertTrue(out["planner"]["present"])
        self.assertIn("First plan", out["planner"]["body"])
        self.assertTrue(out["implementer"]["present"])
        # Latest-per-state: the reworked implementer output wins.
        self.assertIn("Reworked attempt", out["implementer"]["body"])
        self.assertNotIn("Old attempt", out["implementer"]["body"])
        self.assertTrue(out["reviewer"]["present"])
        self.assertEqual(len(out["reviewer"]["findings"]), 2)

    # ---------- empty review ----------

    def test_empty_review_yields_no_findings(self):
        body = ("## Review\n\n"
                "**Recommendation: APPROVE** — 0 blocking, 0 major, 0 minor.\n\n"
                "### Findings\n\n"
                "If you have no findings, say so. No findings.\n\n"
                "### Summary\n\nClean.\n")
        comments = [
            attempt_marker("agent_review", 1, "2026-05-01T10:00:00Z"),
            output(body, "2026-05-01T10:00:01Z"),
        ]
        out = json.loads(_run(comments).stdout)
        self.assertTrue(out["reviewer"]["present"])
        self.assertEqual(out["reviewer"]["findings"], [])

    # ---------- missing source ----------

    def test_missing_source_is_present_false(self):
        comments = [
            attempt_marker("plan", 1, "2026-05-01T09:00:00Z"),
            output("## Plan\n\nA plan.", "2026-05-01T09:00:01Z"),
        ]
        out = json.loads(_run(comments).stdout)
        self.assertTrue(out["planner"]["present"])
        self.assertFalse(out["implementer"]["present"])
        self.assertIsNone(out["implementer"]["body"])
        self.assertIsNone(out["implementer"]["createdAt"])
        self.assertFalse(out["reviewer"]["present"])
        self.assertIsNone(out["reviewer"]["body"])
        self.assertIsNone(out["reviewer"]["recommendation"])
        self.assertEqual(out["reviewer"]["findings"], [])

    # ---------- marker with no paired output ----------

    def test_marker_without_output_is_not_paired(self):
        # An attempt marker immediately followed by another cadence comment
        # (here, the next state marker) has no subagent output.
        comments = [
            attempt_marker("plan", 1, "2026-05-01T09:00:00Z"),
            attempt_marker("implement", 1, "2026-05-01T10:00:00Z"),
            output("## Implementation\n\nWork.", "2026-05-01T10:00:01Z"),
        ]
        out = json.loads(_run(comments).stdout)
        self.assertFalse(out["planner"]["present"])
        self.assertTrue(out["implementer"]["present"])

    # ---------- prior-triage detection ----------

    def test_prior_triage_detected(self):
        marker = ('<!-- cadence:triage {"created":["ENG-42"],"merged":[],'
                  '"dismissed":2} -->\n'
                  "**Cadence triage** — reviewed 3 findings.")
        comments = [
            attempt_marker("agent_review", 1, "2026-05-01T10:00:00Z"),
            output(REVIEW_BODY, "2026-05-01T10:00:01Z"),
            _comment(marker, "2026-05-01T13:00:00Z", user="Human"),
        ]
        out = json.loads(_run(comments).stdout)
        self.assertEqual(len(out["prior_triage"]), 1)
        pt = out["prior_triage"][0]
        self.assertEqual(pt["created_ids"], ["ENG-42"])
        self.assertEqual(pt["merged_ids"], [])
        self.assertEqual(pt["raw"]["dismissed"], 2)
        # The triage marker is a cadence comment — it must not be mistaken for
        # a reviewer/planner/implementer output.
        self.assertTrue(out["reviewer"]["present"])

    # ---------- warning between marker and output ----------

    def test_warning_between_marker_and_output_tolerated(self):
        comments = [
            attempt_marker("agent_review", 1, "2026-05-01T10:00:00Z"),
            warning_comment("2026-05-01T10:00:00.5Z"),
            output(REVIEW_BODY, "2026-05-01T10:00:01Z"),
        ]
        out = json.loads(_run(comments).stdout)
        self.assertTrue(out["reviewer"]["present"])
        self.assertEqual(len(out["reviewer"]["findings"]), 2)

    # ---------- connection-wrap tolerated ----------

    def test_connection_wrap_nodes_shape(self):
        comments = {"nodes": [
            attempt_marker("agent_review", 1, "2026-05-01T10:00:00Z"),
            output(REVIEW_BODY, "2026-05-01T10:00:01Z"),
        ]}
        out = json.loads(_run(comments).stdout)
        self.assertTrue(out["reviewer"]["present"])
        self.assertEqual(len(out["reviewer"]["findings"]), 2)
        self.assertEqual(out["parse_errors"], [])

    # ---------- malformed input ----------

    def test_non_array_input_surfaces_in_parse_errors(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "comments.json"
            p.write_text(json.dumps("not a list"), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(p)],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0)
            out = json.loads(r.stdout)
            self.assertGreaterEqual(len(out["parse_errors"]), 1)
            self.assertFalse(out["reviewer"]["present"])


if __name__ == "__main__":
    unittest.main()
