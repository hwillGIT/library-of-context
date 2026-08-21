from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "write-timeless-technical-prose"
CHECKER = SKILL / "scripts" / "check_github_summary.py"
POLICY = SKILL / "references" / "github-technical-communication.yaml"

VALID_BODY = (
    "<!-- technical-summary:start -->\n"
    "## Plain-English Technical Summary\n\n"
    "<!-- technical-risk:start -->"
    "Cleanup can overlap record publication and expose stale cache state."
    "<!-- technical-risk:end -->\n"
    "<!-- technical-fix:start -->"
    "The ReadWriteGate gives cleanup exclusive access,"
    "<!-- technical-fix:end -->\n"
    "<!-- technical-state:start -->"
    "so SQLite is authoritative when cleanup returns and Redis can remain unavailable."
    "<!-- technical-state:end -->\n\n"
    "**Key Concepts Explained**\n"
    '* **"Race condition":** Two concurrent operations can publish conflicting '
    "record state and make later reads incorrect.\n"
    '* **"ReadWriteGate":** The gate allows shared record writes and gives bulk '
    "cleanup exclusive access, which prevents conflicting operations.\n"
    '* **"SQLite source of truth":** Reads validate disposable cache data against '
    "SQLite, so stale Redis data cannot replace the durable record.\n"
    "<!-- technical-summary:end -->\n\n"
    "## Problem\n\n"
    "The cache can disagree with SQLite.\n"
)


class GitHubTechnicalCommunicationTests(unittest.TestCase):
    def _check_body(self, body: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            body_path = Path(directory) / "body.md"
            body_path.write_text(body, encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(CHECKER),
                    "--body-file",
                    str(body_path),
                    "--policy",
                    str(POLICY),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_valid_summary_passes(self) -> None:
        result = self._check_body(VALID_BODY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_summary_requires_one_or_two_sentences(self) -> None:
        body = VALID_BODY.replace(
            "Cleanup can overlap record publication and expose stale cache state.",
            "Cleanup can overlap record publication. Readers can observe stale state.",
        )
        result = self._check_body(body)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected 1-2", result.stdout)

    def test_summary_requires_three_complete_concept_bullets(self) -> None:
        body = VALID_BODY.replace(
            '* **"SQLite source of truth":** Reads validate disposable cache data '
            "against SQLite, so stale Redis data cannot replace the durable record.\n",
            "",
        )
        result = self._check_body(body)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected 3-8", result.stdout)

    def test_placeholder_content_fails(self) -> None:
        body = VALID_BODY.replace(
            "Cleanup can overlap record publication and expose stale cache state.",
            "REPLACE_WITH_FAILURE_RISK",
        )
        result = self._check_body(body)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("placeholder", result.stdout)

    def test_summary_requires_one_ordered_marker_pair(self) -> None:
        missing = self._check_body(
            VALID_BODY.replace("<!-- technical-summary:start -->\n", "")
        )
        duplicate = self._check_body(
            VALID_BODY.replace(
                "<!-- technical-summary:start -->",
                "<!-- technical-summary:start -->\n<!-- technical-summary:start -->",
            )
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("exactly one", missing.stdout)
        self.assertIn("exactly one", duplicate.stdout)

    def test_triad_markers_require_risk_fix_state_order(self) -> None:
        lines = VALID_BODY.splitlines()
        risk = next(line for line in lines if "technical-risk:start" in line)
        fix = next(line for line in lines if "technical-fix:start" in line)
        risk_index = lines.index(risk)
        fix_index = lines.index(fix)
        lines[risk_index], lines[fix_index] = lines[fix_index], lines[risk_index]
        result = self._check_body("\n".join(lines))
        self.assertEqual(result.returncode, 1)
        self.assertIn("out of order", result.stdout)

    def test_summary_rejects_missing_or_unmarked_triad_text(self) -> None:
        missing = self._check_body(
            VALID_BODY.replace("<!-- technical-fix:start -->", "")
        )
        unmarked = self._check_body(
            VALID_BODY.replace(
                "\n**Key Concepts Explained**",
                "\nUnmarked summary text.\n\n**Key Concepts Explained**",
            )
        )
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(unmarked.returncode, 1)
        self.assertIn("marker pair", missing.stdout)
        self.assertIn("must be inside", unmarked.stdout)

    def test_duplicate_sections_fail(self) -> None:
        body = VALID_BODY.replace(
            "## Plain-English Technical Summary",
            "## Plain-English Technical Summary\n## Plain-English Technical Summary",
        )
        result = self._check_body(body)
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected one", result.stdout)

    def test_duplicate_public_heading_outside_the_block_fails(self) -> None:
        body = VALID_BODY + "\n## Plain-English Technical Summary\n"
        result = self._check_body(body)
        self.assertEqual(result.returncode, 1)
        self.assertIn("one summary heading", result.stdout)

    def test_prefix_fence_and_raw_html_cannot_hide_the_summary(self) -> None:
        prefixed = self._check_body("Visible prefix\n" + VALID_BODY)
        fenced = self._check_body(
            VALID_BODY.replace(
                "<!-- technical-summary:start -->\n",
                "<!-- technical-summary:start -->\n```text\n",
            )
        )
        hidden = self._check_body("<details>\n" + VALID_BODY + "\n</details>")
        raw_html = self._check_body(
            VALID_BODY.replace(
                "Cleanup can overlap record publication",
                "<strong>Cleanup</strong> can overlap record publication",
            )
        )
        for result in (prefixed, fenced, hidden, raw_html):
            with self.subTest(output=result.stdout):
                self.assertEqual(result.returncode, 1)

    def test_long_run_on_summary_fails(self) -> None:
        body = VALID_BODY.replace(
            "Cleanup can overlap record publication and expose stale cache state.",
            " ".join(["conflict"] * 60) + ".",
        )
        result = self._check_body(body)
        self.assertEqual(result.returncode, 1)
        self.assertIn("maximum", result.stdout)

    def test_short_concept_explanation_fails(self) -> None:
        body = VALID_BODY.replace(
            "Two concurrent operations can publish conflicting record state and make "
            "later reads incorrect.",
            "A race occurs.",
        )
        result = self._check_body(body)
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected at least 8", result.stdout)

    def test_concept_terms_require_visible_text_without_controls(self) -> None:
        blank = self._check_body(VALID_BODY.replace('"Race condition"', '"\u00a0"'))
        entity = self._check_body(VALID_BODY.replace('"Race condition"', '"&nbsp;"'))
        controlled = self._check_body(
            VALID_BODY.replace("Race condition", "Race\x1bcondition")
        )
        bidirectional = self._check_body(
            VALID_BODY.replace("Race condition", "Race\u202econdition")
        )
        self.assertEqual(blank.returncode, 1)
        self.assertEqual(entity.returncode, 1)
        self.assertEqual(controlled.returncode, 1)
        self.assertEqual(bidirectional.returncode, 1)
        self.assertIn("visible letter or number", blank.stdout)
        self.assertIn("visible letter or number", entity.stdout)
        self.assertIn("control character", controlled.stdout)
        self.assertIn("control character", bidirectional.stdout)

    def test_concept_explanations_have_sentence_and_total_word_bounds(self) -> None:
        sentence = "One two three four five six seven eight."
        body = VALID_BODY.replace(
            "Two concurrent operations can publish conflicting record state and make "
            "later reads incorrect.",
            " ".join([sentence] * 7),
        )
        result = self._check_body(body)
        self.assertEqual(result.returncode, 1)
        self.assertIn("maximum is 2", result.stdout)
        self.assertIn("maximum is 50", result.stdout)

    def test_bracketed_type_identifier_is_not_a_placeholder(self) -> None:
        body = VALID_BODY.replace('"ReadWriteGate"', '"list[str]"')
        result = self._check_body(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_findings_do_not_echo_pull_request_text(self) -> None:
        secret = "PRIVATE-PULL-REQUEST-TEXT"
        body = VALID_BODY.replace("Race condition", secret * 8)
        result = self._check_body(body)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_unchanged_template_fails(self) -> None:
        template = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
            encoding="utf-8"
        )
        result = self._check_body(template)
        self.assertEqual(result.returncode, 1)
        self.assertIn("placeholder", result.stdout)

    def test_windows_line_endings_and_unicode_pass(self) -> None:
        body = VALID_BODY.replace("Race condition", "竞争条件").replace("\n", "\r\n")
        result = self._check_body(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_non_pull_request_event_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(
                json.dumps({"ref": "refs/heads/main"}), encoding="utf-8"
            )
            result = subprocess.run(
                [sys.executable, "-B", str(CHECKER), "--event", str(event_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not contain", result.stderr)

    def test_github_event_path_accepts_a_pull_request_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(
                json.dumps({"pull_request": {"body": VALID_BODY}}), encoding="utf-8"
            )
            environment = dict(os.environ)
            environment["GITHUB_EVENT_PATH"] = str(event_path)
            result = subprocess.run(
                [sys.executable, "-B", str(CHECKER)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_event_returns_input_error_without_body_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(
                '{"pull_request": {"body": "SECRET"}', encoding="utf-8"
            )
            result = subprocess.run(
                [sys.executable, "-B", str(CHECKER), "--event", str(event_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("SECRET", result.stdout + result.stderr)

    def test_duplicate_policy_key_returns_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.yaml"
            policy_path.write_text(
                POLICY.read_text(encoding="utf-8") + "\nconcept_minimum: 4\n",
                encoding="utf-8",
            )
            body_path = Path(directory) / "body.md"
            body_path.write_text(VALID_BODY, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(CHECKER),
                    "--body-file",
                    str(body_path),
                    "--policy",
                    str(policy_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate keys", result.stderr)

    def test_checker_uses_only_the_standard_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            body_path = Path(directory) / "body.md"
            body_path.write_text(VALID_BODY, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-B",
                    str(CHECKER),
                    "--body-file",
                    str(body_path),
                    "--policy",
                    str(POLICY),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_policy_skill_template_and_workflow_are_connected(self) -> None:
        policy = POLICY.read_text(encoding="utf-8")
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        template = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
            encoding="utf-8"
        )
        workflow = (ROOT / ".github" / "workflows" / "technical-summary.yml").read_text(
            encoding="utf-8"
        )
        general_workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(
            encoding="utf-8"
        )
        codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
        workflow_guide = (ROOT / "docs" / "DEVELOPMENT_WORKFLOW.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("summary_triad:", policy)
        self.assertIn("Failure Risk or Conflict", policy)
        self.assertIn("Mechanical Fix", policy)
        self.assertIn("Guaranteed State at Method Completion", policy)
        self.assertIn("summary_total_word_maximum: 50", policy)
        self.assertIn("concept_explanation_word_minimum: 8", policy)
        self.assertIn("concept_sentence_maximum: 2", policy)
        self.assertIn("concept_total_word_maximum: 50", policy)
        self.assertIn("record reads through ContextCache.get", policy)
        self.assertIn("github-technical-communication.yaml", skill)
        self.assertIn("## Plain-English Technical Summary", template)
        self.assertIn("**Key Concepts Explained**", template)
        self.assertIn("<!-- technical-summary:start -->", template)
        self.assertIn("<!-- technical-summary:end -->", template)
        self.assertIn("<!-- technical-risk:start -->", template)
        self.assertIn("<!-- technical-fix:start -->", template)
        self.assertIn("<!-- technical-state:start -->", template)
        self.assertIn("check_github_summary.py", workflow)
        self.assertIn("GITHUB_EVENT_PATH", workflow)
        self.assertIn("pull_request_target:", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("edited", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("github.event.pull_request.base.sha", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertRegex(workflow, r"actions/checkout@[0-9a-f]{40}")
        self.assertRegex(workflow, r"actions/setup-python@[0-9a-f]{40}")
        self.assertNotRegex(workflow, r"uses:\s+actions/[^@\s]+@v\d")
        self.assertNotIn("pip install", workflow)
        self.assertNotIn("pull_request.head", workflow)
        self.assertNotIn("github.event.pull_request.body", workflow)
        self.assertNotIn("check_github_summary.py", general_workflow)
        self.assertIn("/.github/CODEOWNERS @hwillGIT", codeowners)
        self.assertIn("/.github/workflows/ @hwillGIT", codeowners)
        self.assertIn("write-timeless-technical-prose/ @hwillGIT", codeowners)
        self.assertIn(
            "require the `Technical summary structure` status check", workflow_guide
        )
        self.assertIn("does not enforce", workflow_guide)


if __name__ == "__main__":
    unittest.main()
