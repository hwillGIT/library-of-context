import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
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
