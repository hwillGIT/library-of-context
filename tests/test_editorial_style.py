import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCANNED_SUFFIXES = {".md", ".py", ".ps1", ".toml", ".yaml", ".yml"}
IGNORED_PARTS = {".git", ".venv", ".ruff_cache", "site", "library_of_context.egg-info"}
IGNORED_FILES = {
    Path("tests/test_editorial_style.py"),
    Path("tests/test_technical_language.py"),
    Path(
        ".agents/skills/write-timeless-technical-prose/"
        "references/asd-ste100-software.yaml"
    ),
}
PROHIBITED_PROSE = {
    "requester narration": re.compile(
        r"\b(?:as requested|the (?:user|prompt) (?:asked|requested|said|wanted))\b",
        re.IGNORECASE,
    ),
    "edit narration": re.compile(
        r"\bwe (?:have )?(?:recently )?"
        r"(?:added|changed|updated|rewritten|refactored|moved|renamed)\b",
        re.IGNORECASE,
    ),
    "revision narration": re.compile(
        r"\bthis (?:change|update|revision|refactor|rewrite) "
        r"(?:was|is|makes|improves)\b",
        re.IGNORECASE,
    ),
    "release-relative label": re.compile(
        r"\b(?:(?:this|the) latest|updated|improved) "
        r"(?:design|architecture|implementation|version|documentation|section|code|"
        r"class|system|approach|workflow|pipeline|module|readme|guide)\b",
        re.IGNORECASE,
    ),
    "temporal addition provenance": re.compile(
        r"\b(?:newly|recently) "
        r"(?:added|introduced|created|written|documented|implemented|included)\b|"
        r"\b(?:has|have|was|were) (?:now |recently |newly )?(?:been )?"
        r"(?:added|introduced|removed|renamed|moved|refactored|rewritten)\b",
        re.IGNORECASE,
    ),
    "temporal capability provenance": re.compile(
        r"\b(?:now|currently) (?:also )?"
        r"(?:includes?|contains?|documents?|describes?|covers?|provides?|supports?|"
        r"uses?|implements?|exposes?|offers?)\b",
        re.IGNORECASE,
    ),
    "editorial placement provenance": re.compile(
        r"\b(?:earlier|later|previous|subsequent|following|preceding) "
        r"(?:section|paragraph|chapter|document|text|content|discussion|explanation|"
        r"example)\b|"
        r"\bas (?:noted|described|discussed|explained|mentioned) "
        r"(?:above|below|earlier|previously)\b|"
        r"\b(?:following|after) (?:this|the) "
        r"(?:change|update|revision|addition|refactor|rewrite)\b",
        re.IGNORECASE,
    ),
    "promotional cliche": re.compile(
        r"\b(?:seamless|game-changing|revolutionary|cutting-edge|next-generation|"
        r"unlock|leverage|powerful|cleaner|world-class|exciting|amazing)\b",
        re.IGNORECASE,
    ),
}

TEMPORAL_PROVENANCE_LABELS = {
    "temporal addition provenance",
    "temporal capability provenance",
    "editorial placement provenance",
}

TEMPORAL_PROVENANCE_EXEMPT_FILES = {
    Path(".agents/skills/write-timeless-technical-prose/SKILL.md"),
    Path("AGENTS.md"),
    Path("CHANGELOG.md"),
    Path("CONTRIBUTING.md"),
    Path("ROADMAP.md"),
    Path("docs/DECISION_BRIEF_TEMPLATE.md"),
    Path("docs/STATUS.md"),
    Path("docs/contributing.md"),
    Path("docs/roadmap.md"),
}


def prose_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if (
            path.is_file()
            and path.suffix.lower() in SCANNED_SUFFIXES
            and not IGNORED_PARTS.intersection(relative.parts)
            and relative not in IGNORED_FILES
        ):
            files.append(path)
    return files


class EditorialStyleTests(unittest.TestCase):
    def test_temporal_provenance_rules_preserve_technical_ordering(self) -> None:
        patterns = [PROHIBITED_PROSE[label] for label in TEMPORAL_PROVENANCE_LABELS]
        prohibited = [
            "The helper was added after the refactor.",
            "The service now supports shared caches.",
            "As described above, the worker owns the queue.",
        ]
        allowed = [
            "The event is durable before prompt assembly.",
            "The worker resumes after a process restart.",
            "The current request replaces the previous desk.",
        ]

        for text in prohibited:
            self.assertTrue(any(pattern.search(text) for pattern in patterns), text)
        for text in allowed:
            self.assertFalse(any(pattern.search(text) for pattern in patterns), text)

    def test_repository_prose_has_no_process_narration_or_promotional_cliches(
        self,
    ) -> None:
        failures: list[str] = []
        for path in prose_files():
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(ROOT)
            for label, pattern in PROHIBITED_PROSE.items():
                if (
                    label in TEMPORAL_PROVENANCE_LABELS
                    and relative in TEMPORAL_PROVENANCE_EXEMPT_FILES
                ):
                    continue
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    failures.append(f"{relative}:{line}: {label}: {match.group(0)!r}")

        self.assertEqual([], failures, "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
