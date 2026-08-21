from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from check_technical_prose import CONTRACTION, SENTENCE_END, WORD, load_profile

DEFAULT_POLICY = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "github-technical-communication.yaml"
)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
MARKDOWN_LINK = re.compile(r"\[([^]]+)]\([^)]+\)")
CONCEPT_BULLET = re.compile(
    r'^\*\s+\*\*"(?P<term>[^"\n]+)":\*\*\s+(?P<explanation>\S.+)$'
)
PLACEHOLDER = re.compile(r"\b(?:REPLACE_WITH_[A-Z0-9_]+|tbd|todo)\b", re.IGNORECASE)
FENCE_MARKER = re.compile(r"(?m)^\s*(?:```|~~~)")
RAW_HTML_TAG = re.compile(r"<(?!\!--)[A-Za-z!/][^>\n]*>")
START_MARKER = "<!-- technical-summary:start -->"
END_MARKER = "<!-- technical-summary:end -->"
TRIAD_MARKERS = ("technical-risk", "technical-fix", "technical-state")
ALLOWED_POLICY_KEYS = {
    "applies_to",
    "body_byte_maximum",
    "concept_bullet_format",
    "concept_explanation_word_minimum",
    "concept_maximum",
    "concept_minimum",
    "concept_sentence_maximum",
    "concept_sentence_word_maximum",
    "concept_term_character_maximum",
    "concept_total_word_maximum",
    "concepts_heading",
    "few_shot_concepts",
    "few_shot_input",
    "few_shot_summary",
    "grounded_term_examples",
    "guarantee_boundary_rule",
    "guarantee_precision_rule",
    "mechanism_precision_rule",
    "noun_to_verb_examples",
    "objective",
    "permit_contractions_in_prose",
    "permit_semicolon_in_prose",
    "policy_name",
    "policy_schema_version",
    "preserve_technical_depth",
    "require_exact_section_order",
    "require_triad_markers",
    "require_unique_sections",
    "role",
    "semantic_review_rule",
    "summary_heading",
    "summary_sentence_maximum",
    "summary_sentence_minimum",
    "summary_sentence_word_maximum",
    "summary_total_word_maximum",
    "summary_total_word_minimum",
    "summary_triad",
    "translation_heuristics",
    "triad_markers",
}


def _finding(code: str, message: str) -> str:
    return f"{code}: {message}"


def _profile_keys(text: str) -> list[str]:
    keys: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or raw_line.startswith("  - "):
            continue
        if ":" not in stripped:
            raise ValueError("policy contains a line without a key")
        keys.append(stripped.split(":", 1)[0].strip())
    return keys


def _integer(policy: dict[str, object], key: str, minimum: int, maximum: int) -> int:
    value = policy.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"policy {key} must be an integer from {minimum} to {maximum}")
    return value


def _validate_numeric_policy(policy: dict[str, object]) -> None:
    sentence_minimum = _integer(policy, "summary_sentence_minimum", 1, 2)
    sentence_maximum = _integer(policy, "summary_sentence_maximum", 1, 2)
    if sentence_minimum > sentence_maximum:
        raise ValueError("summary sentence minimum exceeds maximum")
    sentence_words = _integer(policy, "summary_sentence_word_maximum", 5, 25)
    total_minimum = _integer(policy, "summary_total_word_minimum", 1, 50)
    total_maximum = _integer(policy, "summary_total_word_maximum", 1, 50)
    if total_minimum > total_maximum:
        raise ValueError("summary word minimum exceeds maximum")
    if total_maximum > sentence_maximum * sentence_words:
        raise ValueError("summary word maximum exceeds the sentence limits")
    concept_minimum = _integer(policy, "concept_minimum", 3, 20)
    concept_maximum = _integer(policy, "concept_maximum", 3, 20)
    if concept_minimum > concept_maximum:
        raise ValueError("concept minimum exceeds maximum")
    _integer(policy, "concept_term_character_maximum", 1, 200)
    _integer(policy, "concept_explanation_word_minimum", 1, 25)
    _integer(policy, "concept_sentence_maximum", 1, 2)
    _integer(policy, "concept_sentence_word_maximum", 5, 25)
    _integer(policy, "concept_total_word_maximum", 8, 50)
    _integer(policy, "body_byte_maximum", 1024, 1_048_576)


def _validate_boolean_policy(policy: dict[str, object]) -> None:
    keys = (
        "permit_contractions_in_prose",
        "permit_semicolon_in_prose",
        "require_exact_section_order",
        "require_triad_markers",
        "require_unique_sections",
    )
    invalid = [key for key in keys if type(policy.get(key)) is not bool]
    if invalid:
        raise ValueError(f"policy {invalid[0]} must be true or false")
    if policy["permit_contractions_in_prose"]:
        raise ValueError("GitHub summaries must not permit contractions")
    if policy["permit_semicolon_in_prose"]:
        raise ValueError("GitHub summaries must not permit semicolons")
    required = (
        "require_exact_section_order",
        "require_triad_markers",
        "require_unique_sections",
    )
    if not all(policy[key] for key in required):
        raise ValueError("GitHub summary structure requirements must be true")


def load_github_policy(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    keys = _profile_keys(text)
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"policy contains duplicate keys: {', '.join(duplicates)}")
    unknown = sorted(set(keys) - ALLOWED_POLICY_KEYS)
    if unknown:
        raise ValueError(f"policy contains unknown keys: {', '.join(unknown)}")

    policy = load_profile(path)
    if policy.get("policy_schema_version") != 1:
        raise ValueError("policy_schema_version must be 1")
    _validate_numeric_policy(policy)
    _validate_boolean_policy(policy)
    if policy.get("triad_markers") != list(TRIAD_MARKERS):
        raise ValueError("policy triad_markers must use the required order")
    for key in ("summary_heading", "concepts_heading"):
        if not isinstance(policy.get(key), str) or not str(policy[key]).strip():
            raise ValueError(f"policy {key} must be nonempty text")
    return policy


def _visible_text(markdown: str) -> str:
    text = HTML_COMMENT.sub("", markdown)
    text = MARKDOWN_LINK.sub(r"\1", text)
    text = re.sub(r"[`*_~]", "", text)
    text = html.unescape(text)
    return " ".join(text.split())


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in SENTENCE_END.split(text) if item.strip()]


def _has_placeholder(markdown: str) -> bool:
    return bool(PLACEHOLDER.search(markdown))


def _summary_block(body: str) -> tuple[str | None, list[str]]:
    if body.count(START_MARKER) != 1 or body.count(END_MARKER) != 1:
        return None, [
            _finding(
                "TS001",
                "technical summary requires exactly one start marker "
                "and one end marker",
            )
        ]
    if not body.lstrip().startswith(START_MARKER):
        return None, [
            _finding(
                "TS003",
                "technical summary start marker must be the first body content",
            )
        ]
    start = body.index(START_MARKER) + len(START_MARKER)
    end = body.index(END_MARKER)
    if end <= start:
        return None, [_finding("TS002", "technical summary markers are reversed")]
    return body[start:end], []


def _heading_pattern(heading: str, *, bold: bool = False) -> re.Pattern[str]:
    if bold:
        return re.compile(rf"(?im)^\*\*{re.escape(heading)}\*\*\s*$")
    return re.compile(rf"(?im)^##\s+{re.escape(heading)}\s*$")


def _global_heading_findings(body: str, policy: dict[str, object]) -> list[str]:
    summary_heading = str(policy["summary_heading"])
    concepts_heading = str(policy["concepts_heading"])
    findings: list[str] = []
    if len(_heading_pattern(summary_heading).findall(body)) != 1:
        findings.append(
            _finding("TS004", "pull-request body must contain one summary heading")
        )
    concept_pattern = _heading_pattern(concepts_heading, bold=True)
    if len(concept_pattern.findall(body)) != 1:
        findings.append(
            _finding("TS005", "pull-request body must contain one concepts heading")
        )
    return findings


def _markup_findings(block: str) -> list[str]:
    findings: list[str] = []
    has_control = any(
        character not in "\r\n\t" and unicodedata.category(character).startswith("C")
        for character in block
    )
    if has_control:
        findings.append(
            _finding("TS006", "technical summary contains a control character")
        )
    if FENCE_MARKER.search(block):
        findings.append(_finding("TS007", "technical summary contains a code fence"))
    if RAW_HTML_TAG.search(block):
        findings.append(_finding("TS008", "technical summary contains a raw HTML tag"))
    return findings


def _sections(
    block: str, policy: dict[str, object]
) -> tuple[str | None, str | None, list[str]]:
    summary_heading = str(policy["summary_heading"])
    concepts_heading = str(policy["concepts_heading"])
    summary_matches = list(_heading_pattern(summary_heading).finditer(block))
    concept_pattern = _heading_pattern(concepts_heading, bold=True)
    concept_matches = list(concept_pattern.finditer(block))
    findings: list[str] = []
    if len(summary_matches) != 1:
        findings.append(
            _finding("TS010", f'expected one "## {summary_heading}" heading')
        )
    if len(concept_matches) != 1:
        findings.append(
            _finding("TS011", f'expected one "**{concepts_heading}**" heading')
        )
    if findings:
        return None, None, findings
    summary_match = summary_matches[0]
    concept_match = concept_matches[0]
    if block[: summary_match.start()].strip():
        return (
            None,
            None,
            [_finding("TS013", "only whitespace can precede the summary heading")],
        )
    if summary_match.end() >= concept_match.start():
        return (
            None,
            None,
            [_finding("TS012", "summary and concept sections are out of order")],
        )
    summary = block[summary_match.end() : concept_match.start()]
    concepts = block[concept_match.end() :]
    return summary, concepts, []


def _check_triad(section: str) -> list[str]:
    findings: list[str] = []
    ranges: list[tuple[int, int]] = []
    full_ranges: list[tuple[int, int]] = []
    for name in TRIAD_MARKERS:
        start_marker = f"<!-- {name}:start -->"
        end_marker = f"<!-- {name}:end -->"
        if section.count(start_marker) != 1 or section.count(end_marker) != 1:
            findings.append(
                _finding("TS020", f"summary requires one {name} marker pair")
            )
            continue
        marker_start = section.index(start_marker)
        start = marker_start + len(start_marker)
        end = section.index(end_marker)
        if end <= start:
            findings.append(
                _finding("TS021", f"{name} marker pair is reversed or empty")
            )
            continue
        content = section[start:end]
        if not _visible_text(content) or _has_placeholder(content):
            findings.append(
                _finding("TS022", f"{name} content is empty or placeholder text")
            )
        ranges.append((start, end))
        full_ranges.append((marker_start, end + len(end_marker)))
    if len(ranges) == len(TRIAD_MARKERS):
        positions = [item for pair in ranges for item in pair]
        if positions != sorted(positions):
            findings.append(
                _finding(
                    "TS023", "risk, fix, and guaranteed-state markers are out of order"
                )
            )
    unmarked = section
    for start, end in sorted(full_ranges, reverse=True):
        unmarked = unmarked[:start] + unmarked[end:]
    if _visible_text(unmarked):
        findings.append(
            _finding(
                "TS024", "summary text must be inside the risk, fix, or state markers"
            )
        )
    return findings


def _language_findings(text: str, *, prefix: str, maximum_words: int) -> list[str]:
    findings: list[str] = []
    if CONTRACTION.search(text):
        findings.append(_finding(f"{prefix}1", "text contains a contraction"))
    if ";" in text:
        findings.append(_finding(f"{prefix}2", "text contains a semicolon"))
    for sentence in _sentences(text):
        count = len(WORD.findall(sentence))
        if count > maximum_words:
            findings.append(
                _finding(
                    f"{prefix}3",
                    f"sentence has {count} words; maximum is {maximum_words}",
                )
            )
    return findings


def _check_summary(section: str, policy: dict[str, object]) -> list[str]:
    findings = _check_triad(section)
    summary = _visible_text(section)
    if not summary or _has_placeholder(section):
        return findings + [
            _finding(
                "TS030", "plain-English summary is empty or contains a placeholder"
            )
        ]
    sentences = _sentences(summary)
    minimum = int(policy["summary_sentence_minimum"])
    maximum = int(policy["summary_sentence_maximum"])
    if not minimum <= len(sentences) <= maximum:
        findings.append(
            _finding(
                "TS031",
                f"plain-English summary has {len(sentences)} sentences; "
                f"expected {minimum}-{maximum}",
            )
        )
    if not re.search(r"[.!?][\"')\]]?$", summary):
        findings.append(
            _finding("TS032", "plain-English summary must end as a sentence")
        )
    words = len(WORD.findall(summary))
    word_minimum = int(policy["summary_total_word_minimum"])
    word_maximum = int(policy["summary_total_word_maximum"])
    if not word_minimum <= words <= word_maximum:
        findings.append(
            _finding(
                "TS033",
                f"plain-English summary has {words} words; "
                f"expected {word_minimum}-{word_maximum}",
            )
        )
    findings.extend(
        _language_findings(
            summary,
            prefix="TS04",
            maximum_words=int(policy["summary_sentence_word_maximum"]),
        )
    )
    return findings


def _concept_detail_findings(
    term: str, explanation: str, policy: dict[str, object]
) -> tuple[bool, list[str]]:
    findings: list[str] = []
    visible_term = html.unescape(term)
    if not any(character.isalnum() for character in visible_term):
        return False, [
            _finding("TS055", "concept term must contain a visible letter or number")
        ]
    term_limit = int(policy["concept_term_character_maximum"])
    if len(term) > term_limit:
        findings.append(
            _finding(
                "TS050",
                f"concept term exceeds {term_limit} characters",
            )
        )
    explanation_words = len(WORD.findall(explanation))
    explanation_minimum = int(policy["concept_explanation_word_minimum"])
    if explanation_words < explanation_minimum:
        findings.append(
            _finding(
                "TS051",
                f"concept explanation has {explanation_words} words; "
                f"expected at least {explanation_minimum}",
            )
        )
    total_word_maximum = int(policy["concept_total_word_maximum"])
    if explanation_words > total_word_maximum:
        findings.append(
            _finding(
                "TS056",
                f"concept explanation has {explanation_words} words; "
                f"maximum is {total_word_maximum}",
            )
        )
    explanation_sentences = _sentences(explanation)
    sentence_maximum = int(policy["concept_sentence_maximum"])
    if len(explanation_sentences) > sentence_maximum:
        findings.append(
            _finding(
                "TS057",
                f"concept explanation has {len(explanation_sentences)} sentences; "
                f"maximum is {sentence_maximum}",
            )
        )
    if not re.search(r"[.!?][\"')\]]?$", explanation):
        findings.append(_finding("TS052", "concept explanation must end as a sentence"))
    findings.extend(
        _language_findings(
            explanation,
            prefix="TS06",
            maximum_words=int(policy["concept_sentence_word_maximum"]),
        )
    )
    return True, findings


def _check_concepts(section: str, policy: dict[str, object]) -> list[str]:
    valid_count = 0
    malformed = False
    findings: list[str] = []
    for line in HTML_COMMENT.sub("", section).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = CONCEPT_BULLET.fullmatch(stripped)
        if match is None or _has_placeholder(stripped):
            malformed = True
            continue
        term = match.group("term").strip()
        explanation = _visible_text(match.group("explanation"))
        term_is_valid, detail_findings = _concept_detail_findings(
            term, explanation, policy
        )
        findings.extend(detail_findings)
        if term_is_valid:
            valid_count += 1
    minimum = int(policy["concept_minimum"])
    maximum = int(policy["concept_maximum"])
    if not minimum <= valid_count <= maximum:
        findings.append(
            _finding(
                "TS053",
                f"Key Concepts Explained has {valid_count} valid bullets; "
                f"expected {minimum}-{maximum}",
            )
        )
    if malformed:
        findings.append(
            _finding(
                "TS054",
                'concept bullets must use * **"Term":** definition and runtime impact',
            )
        )
    return findings


def check_pull_request_body(body: str, policy: dict[str, object]) -> list[str]:
    size = len(body.encode("utf-8"))
    maximum = int(policy["body_byte_maximum"])
    if size > maximum:
        return [
            _finding(
                "TS000", f"pull-request body has {size} bytes; maximum is {maximum}"
            )
        ]
    block, findings = _summary_block(body)
    if block is None:
        return findings
    findings.extend(_global_heading_findings(body, policy))
    findings.extend(_markup_findings(block))
    summary, concepts, section_findings = _sections(block, policy)
    findings.extend(section_findings)
    if summary is not None:
        findings.extend(_check_summary(summary, policy))
    if concepts is not None:
        findings.extend(_check_concepts(concepts, policy))
    return findings


def _event_body(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("event JSON root must be an object")
    if "pull_request" not in payload:
        raise ValueError("event does not contain a pull_request object")
    pull_request = payload["pull_request"]
    if not isinstance(pull_request, dict):
        raise ValueError("event pull_request must be an object")
    body = pull_request.get("body")
    if not isinstance(body, str):
        raise ValueError("event pull_request.body must be text")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a pull request body against the technical summary policy."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--event", type=Path)
    source.add_argument("--body-file", type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    arguments = parser.parse_args(argv)
    try:
        policy = load_github_policy(arguments.policy)
        if arguments.body_file is not None:
            body = arguments.body_file.read_text(encoding="utf-8")
        else:
            event_path = arguments.event
            if event_path is None:
                value = os.environ.get("GITHUB_EVENT_PATH")
                event_path = Path(value) if value else None
            if event_path is None:
                raise ValueError("provide --event, --body-file, or GITHUB_EVENT_PATH")
            body = _event_body(event_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError) as error:
        print(f"technical-summary-input: {error}", file=sys.stderr)
        return 2

    findings = check_pull_request_body(body, policy)
    for finding in findings:
        print(f"technical-summary[{finding}]")
    if findings:
        return 1
    print("Pull-request technical summary check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
