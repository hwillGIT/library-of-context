import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def test_timeless_prose_rule_and_skill_are_discoverable(self) -> None:
        skill = (
            ROOT / ".agents" / "skills" / "write-timeless-technical-prose" / "SKILL.md"
        )
        metadata = skill.parent / "agents" / "openai.yaml"

        self.assertTrue(skill.is_file())
        self.assertTrue(metadata.is_file())
        skill_text = skill.read_text(encoding="utf-8")
        self.assertIn("name: write-timeless-technical-prose", skill_text)
        self.assertIn("one coherent editorial present", skill_text)
        self.assertIn("Remove temporal provenance", skill_text)
        self.assertNotIn("TODO", skill_text)

        for source in ("AGENTS.md", "CONTRIBUTING.md", "docs/contributing.md"):
            with self.subTest(source=source):
                text = (ROOT / source).read_text(encoding="utf-8")
                self.assertIn("write-timeless-technical-prose/SKILL.md", text)
                self.assertRegex(text, r"relative to (?:another|other content)")

    def test_related_work_is_in_site_navigation(self) -> None:
        related_work = ROOT / "docs" / "RELATED_WORK.md"

        self.assertTrue(related_work.is_file())
        self.assertIn(
            "- Related work and landscape: RELATED_WORK.md",
            (ROOT / "mkdocs.yml").read_text(encoding="utf-8"),
        )

    def test_related_work_has_required_local_links(self) -> None:
        text = (ROOT / "docs" / "RELATED_WORK.md").read_text(encoding="utf-8")

        for target in (
            "STATUS.md",
            "architecture.md",
            "WHY_THE_ROADMAP.md",
            "roadmap.md",
            "DECISION_BRIEF_TEMPLATE.md",
        ):
            with self.subTest(target=target):
                self.assertIn(f"]({target})", text)
                self.assertTrue((ROOT / "docs" / target).is_file())

        for image in (
            "context-management-landscape.png",
            "research-synthesis.png",
        ):
            with self.subTest(image=image):
                self.assertIn(f"]({image})", text)
                self.assertTrue((ROOT / "docs" / image).is_file())

    def test_entry_documents_link_to_related_work(self) -> None:
        expected_links = {
            "README.md": "docs/RELATED_WORK.md",
            "ARCHITECTURE.md": "docs/RELATED_WORK.md",
            "ROADMAP.md": "docs/RELATED_WORK.md",
            "docs/index.md": "RELATED_WORK.md",
            "docs/architecture.md": "RELATED_WORK.md",
            "docs/roadmap.md": "RELATED_WORK.md",
        }

        for source, target in expected_links.items():
            with self.subTest(source=source):
                text = (ROOT / source).read_text(encoding="utf-8")
                self.assertIn(f"]({target})", text)


if __name__ == "__main__":
    unittest.main()
