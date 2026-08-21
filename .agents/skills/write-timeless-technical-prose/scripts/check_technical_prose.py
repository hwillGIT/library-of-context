from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROFILE = (
    Path(__file__).resolve().parents[1] / "references" / "asd-ste100-software.yaml"
)
WORD = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
SENTENCE_END = re.compile(r"(?<=[.!?])(?:\s+|$)")
INLINE_CODE = re.compile(r"`[^`]*`")
IMAGE = re.compile(r"!\[[^]]*]\([^)]+\)")
LINK = re.compile(r"\[([^]]+)]\([^)]+\)")
URL = re.compile(r"https?://\S+")
CONTRACTION = re.compile(
    r"\b(?:can't|cannot've|couldn't|didn't|doesn't|don't|hadn't|hasn't|haven't|"
    r"isn't|mustn't|shan't|shouldn't|wasn't|weren't|won't|wouldn't|"
    r"[A-Za-z]+(?:n't|'re|'ve|'ll|'d|'m))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    line: int
    rule: str
    text: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.text}"


def load_profile(path: Path) -> dict[str, object]:
    """Read the flat scalar and list values in the repository profile."""

    profile: dict[str, object] = {}
    active_list: list[str] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if raw_line.startswith("  - "):
            if active_list is None:
                raise ValueError(f"list item has no key: {raw_line}")
            active_list.append(line[2:].strip())
            continue
        if ":" not in line:
            raise ValueError(f"invalid profile line: {raw_line}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            active_list = []
            profile[key] = active_list
            continue
        active_list = None
        if value.casefold() in {"true", "false"}:
            profile[key] = value.casefold() == "true"
        elif value.isdigit():
            profile[key] = int(value)
        else:
            profile[key] = value
    return profile


def _plain_text(line: str) -> str:
    text = IMAGE.sub("", line)
    text = INLINE_CODE.sub(" IDENTIFIER ", text)
    text = LINK.sub(r"\1", text)
    text = URL.sub(" URL ", text)
    text = re.sub(r"^\s*(?:[-+*]|\d+[.)])\s+", "", text)
    text = re.sub(r"[*_~]", "", text)
    return " ".join(text.split())


def _eligible_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("#", ">", "<!--", "-->", "<svg", "</svg")):
        return False
    if stripped.startswith("[") and "]:" in stripped:
        return False
    if "|" in stripped and stripped.count("|") >= 2:
        return False
    return True


def _paragraph_findings(
    path: Path,
    paragraph: list[tuple[int, str]],
    *,
    max_words: int,
    max_sentences: int,
) -> list[Finding]:
    if not paragraph:
        return []
    start_line = paragraph[0][0]
    text = " ".join(item[1] for item in paragraph)
    sentences = [item.strip() for item in SENTENCE_END.split(text) if item.strip()]
    findings: list[Finding] = []
    if len(sentences) > max_sentences:
        findings.append(
            Finding(
                path,
                start_line,
                "paragraph-length",
                f"{len(sentences)} sentences, maximum {max_sentences}",
            )
        )
    for sentence in sentences:
        word_count = len(WORD.findall(sentence))
        if word_count > max_words:
            findings.append(
                Finding(
                    path,
                    start_line,
                    "sentence-length",
                    f"{word_count} words, maximum {max_words}: {sentence[:120]}",
                )
            )
    return findings


def _line_findings(
    path: Path,
    line_number: int,
    raw_line: str,
    text: str,
    profile: dict[str, object],
) -> list[Finding]:
    findings: list[Finding] = []
    if not bool(profile["permit_semicolon_in_prose"]) and ";" in text:
        findings.append(Finding(path, line_number, "semicolon", text[:160]))
    if not bool(profile["permit_contractions_in_prose"]) and CONTRACTION.search(text):
        findings.append(Finding(path, line_number, "contraction", text[:160]))
    lowered = text.casefold()
    for item in profile.get("prohibited_vague_terms", []):  # type: ignore[union-attr]
        term = str(item)
        if re.search(rf"\b{re.escape(term.casefold())}\b", lowered):
            findings.append(
                Finding(path, line_number, "vague-term", f"prohibited term: {term}")
            )
    if re.match(r"^\d+[.)]\s+", raw_line.lstrip()):
        maximum = int(profile["procedural_sentence_max_words"])
        sentences = [item.strip() for item in SENTENCE_END.split(text) if item.strip()]
        for sentence in sentences:
            count = len(WORD.findall(sentence))
            if count > maximum:
                findings.append(
                    Finding(
                        path,
                        line_number,
                        "procedure-length",
                        f"{count} words, maximum {maximum}: {sentence[:120]}",
                    )
                )
    return findings


def _prose_lines(path: Path) -> list[tuple[int, str, str, bool]]:
    output: list[tuple[int, str, str, bool]] = []
    in_fence = False
    fence_marker = ""
    in_frontmatter = False
    for number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if number == 1 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            in_frontmatter = stripped != "---"
            continue
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if in_fence and marker == fence_marker:
                in_fence = False
                fence_marker = ""
            elif not in_fence:
                in_fence = True
                fence_marker = marker
            output.append((number, raw_line, "", True))
            continue
        if in_fence:
            continue
        if not _eligible_line(raw_line):
            output.append((number, raw_line, "", True))
            continue
        text = _plain_text(raw_line)
        boundary = not text or stripped.endswith((".", "!", "?", ":"))
        boundary = boundary or raw_line.lstrip().startswith(("- ", "* ", "+ "))
        output.append((number, raw_line, text, boundary))
    return output


def check_markdown(path: Path, profile: dict[str, object]) -> list[Finding]:
    max_words = int(profile["descriptive_sentence_max_words"])
    max_paragraph_sentences = int(profile["paragraph_max_sentences"])
    findings: list[Finding] = []
    paragraph: list[tuple[int, str]] = []

    def flush_paragraph() -> None:
        findings.extend(
            _paragraph_findings(
                path,
                paragraph,
                max_words=max_words,
                max_sentences=max_paragraph_sentences,
            )
        )
        paragraph.clear()

    for number, raw_line, text, boundary in _prose_lines(path):
        if not text:
            flush_paragraph()
            continue
        findings.extend(_line_findings(path, number, raw_line, text, profile))
        paragraph.append((number, text))
        if boundary:
            flush_paragraph()
    flush_paragraph()
    return findings


def markdown_files(inputs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in inputs:
        if path.is_dir():
            files.extend(
                item
                for item in path.rglob("*.md")
                if not {
                    ".git",
                    ".venv",
                    "build",
                    "dist",
                    "site",
                }.intersection(item.parts)
                and not any(part.endswith(".egg-info") for part in item.parts)
            )
        elif path.suffix.casefold() == ".md":
            files.append(path)
    return sorted(set(files))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Markdown against the Library ASD-STE100 software profile."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    arguments = parser.parse_args(argv)
    profile = load_profile(arguments.profile)
    findings: list[Finding] = []
    for path in markdown_files(arguments.paths):
        findings.extend(check_markdown(path, profile))
    for finding in findings:
        print(finding.format())
    if findings:
        print(f"Technical language check found {len(findings)} violation(s).")
        return 1
    print("Technical language check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
