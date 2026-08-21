from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "write-timeless-technical-prose"
CHECKER = SKILL / "scripts" / "check_technical_prose.py"
PROFILE = SKILL / "references" / "asd-ste100-software.yaml"


class TechnicalLanguageTests(unittest.TestCase):
    def _check(self, *paths: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(CHECKER), *(str(path) for path in paths)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_skill_profile_and_reader_guidance_are_connected(self) -> None:
        self.assertTrue(CHECKER.is_file())
        self.assertTrue(PROFILE.is_file())
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        navigation = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        self.assertIn("references/asd-ste100-software.yaml", skill_text)
        self.assertIn("ASD-STE100", skill_text)
        self.assertIn("Glossary: GLOSSARY.md", navigation)
        self.assertIn("Technical language rules: TECHNICAL_LANGUAGE.md", navigation)

    def test_all_development_agents_receive_the_translation_contract(self) -> None:
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        profile_text = PROFILE.read_text(encoding="utf-8")
        agent_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        for text in (skill_text, profile_text, agent_text):
            with self.subTest(source=text[:40]):
                self.assertIn("Part 1: Plain-English Translation", text)
                self.assertIn("Part 2: Key Concepts Explained", text)

        self.assertIn("principal technical writer", skill_text.casefold())
        self.assertIn("concurrency specialist", skill_text.casefold())
        self.assertIn("subject-verb-object", skill_text)
        self.assertIn("preserve_concurrency_semantics:", profile_text)
        self.assertIn("Every development agent must read and apply", agent_text)
        self.assertIn("When a user supplies a passage for translation", agent_text)
        skill_metadata = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: true", skill_metadata)

    def test_checker_accepts_clear_text_and_rejects_known_violations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clear = root / "clear.md"
            clear.write_text(
                "# Clear text\n\nThe worker reads one event. The worker stores the result.\n",
                encoding="utf-8",
            )
            unclear = root / "unclear.md"
            unclear.write_text(
                "# Unclear text\n\n"
                "The world's most powerful service can't fail; it provides a seamless "
                "and scalable solution that performs every difficult operation without "
                "a stated workload, limit, condition, or measured result for the reader.\n",
                encoding="utf-8",
            )
            clear_result = self._check(clear)
            unclear_result = self._check(unclear)

        self.assertEqual(clear_result.returncode, 0, clear_result.stdout)
        self.assertNotEqual(unclear_result.returncode, 0)
        self.assertIn("contraction", unclear_result.stdout)
        self.assertIn("semicolon", unclear_result.stdout)
        self.assertIn("vague-term", unclear_result.stdout)
        self.assertIn("sentence-length", unclear_result.stdout)

    def test_repository_markdown_follows_the_language_profile(self) -> None:
        result = self._check(ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
