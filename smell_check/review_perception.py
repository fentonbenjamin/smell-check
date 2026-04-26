"""Review perception — structural + lexical perception for analytical prose.

This is the review-native parallel to epistemic_tagger.py.
The tagger smells conversations. This smells reviews, specs, and critiques.

Architecture:
  conversation → epistemic_tagger (clause cues) → mother_types → sieve
  review/spec  → review_perception (structure + lexical families) → sieve

Same judgment layer. Different perceiver.
The tagger uses phrase matching. This uses section structure + word families.

Pure. No I/O. No model calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Lexical families — word classes, not exact phrases
# ---------------------------------------------------------------------------

RISK_FAMILY = frozenset({
    "risk", "risks", "concern", "concerns", "issue", "issues",
    "problem", "problems", "brittle", "fragile", "fragility",
    "sustainability", "unsustainable", "dangerous", "problematic",
    "vulnerability", "vulnerabilities", "regression", "regressions",
    "drift", "drifting", "stale", "staleness", "broken",
    "failure", "failing", "fails", "false confidence",
})

OWNERSHIP_FAMILY = frozenset({
    "ownership", "owner", "owns", "owned",
    "blurred", "unclear", "ambiguous", "diffuse",
    "responsibility", "responsible", "accountable",
    "who owns", "no owner", "nobody owns",
    "single source of truth", "source of truth",
    "canonical", "authoritative",
})

ACTION_FAMILY = frozenset({
    "fix", "fixing", "fixed", "resolve", "resolving",
    "clean up", "cleanup", "remove", "archive",
    "migrate", "migration", "move", "relocate",
    "update", "upgrade", "replace", "refactor",
    "add", "implement", "build", "create",
    "freeze", "lock", "pin", "stabilize",
})

QUALITY_FAMILY = frozenset({
    "maintainability", "maintainable", "readable", "readability",
    "simpler", "cleaner", "clearer", "easier",
    "sustainable", "durable", "robust", "resilient",
    "coupled", "decoupled", "tangled", "modular",
    "testable", "tested", "untested", "coverage",
    "heuristic", "heuristics", "ad hoc",
})

SEVERITY_FAMILY = frozenset({
    "p0", "p1", "p2", "p3", "p4",
    "critical", "blocker", "blocking",
    "high", "medium", "low",
    "urgent", "important",
})

ALL_FAMILIES = {
    "risk": RISK_FAMILY,
    "ownership": OWNERSHIP_FAMILY,
    "action": ACTION_FAMILY,
    "quality": QUALITY_FAMILY,
    "severity": SEVERITY_FAMILY,
}


# ---------------------------------------------------------------------------
# Section detection — structural parsing
# ---------------------------------------------------------------------------

@dataclass
class Section:
    """A detected section in the input."""
    kind: str           # findings, risks, recommendations, overall, questions, assumptions
    title: str          # raw header text
    start: int          # char offset
    end: int            # char offset
    items: list[str] = field(default_factory=list)  # parsed items within section


# Section header patterns
_SECTION_PATTERNS = [
    (re.compile(r"^\*\*([^*]+)\*\*\s*$", re.MULTILINE), None),  # **Header**
    (re.compile(r"^##\s+(.+)$", re.MULTILINE), None),            # ## Header
    (re.compile(r"^#\s+(.+)$", re.MULTILINE), None),             # # Header
]

# Map header text to section kind
_HEADER_TO_KIND = {
    "findings": "findings",
    "finding": "findings",
    "issues": "findings",
    "problems": "findings",
    "bugs": "findings",
    "risks": "risks",
    "concerns": "risks",
    "open questions": "questions",
    "questions": "questions",
    "assumptions": "assumptions",
    "recommendations": "recommendations",
    "recommended": "recommendations",
    "recommendation": "recommendations",
    "what i would": "recommendations",
    "what i'd": "recommendations",
    "next steps": "recommendations",
    "cleanup": "recommendations",
    "overall": "overall",
    "summary": "overall",
    "my blunt read": "overall",
    "my read": "overall",
    "bottom line": "overall",
    "what's strong": "positive",
    "what's better": "positive",
    "what improved": "positive",
    "what's good": "positive",
    "what's still wrong": "findings",
    "what's wrong": "findings",
    "what's still missing": "findings",
}

# Numbered item pattern: "1. ", "2. ", "- ", "* "
_ITEM_RE = re.compile(r"^\s*(?:\d+\.|\-|\*)\s+(.+)", re.MULTILINE)

# Severity marker: [P1], [P2], etc.
_SEVERITY_RE = re.compile(r"\[([Pp]\d)\]")


def parse_sections(text: str) -> list[Section]:
    """Parse structural sections from review/spec text. Pure."""
    sections: list[Section] = []

    # Find all headers
    headers: list[tuple[int, int, str]] = []  # (start, end, title)
    for pattern, _ in _SECTION_PATTERNS:
        for m in pattern.finditer(text):
            headers.append((m.start(), m.end(), m.group(1).strip()))

    # Sort by position
    headers.sort(key=lambda h: h[0])

    # Build sections
    for i, (start, end, title) in enumerate(headers):
        # Section extends to next header or end of text
        section_end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        section_text = text[end:section_end]

        # Classify section kind
        title_lower = title.lower().strip("*# ")
        kind = "unknown"
        for key, section_kind in _HEADER_TO_KIND.items():
            if key in title_lower:
                kind = section_kind
                break

        # Extract items
        items = []
        for m in _ITEM_RE.finditer(section_text):
            items.append(m.group(1).strip())

        # If no numbered items, treat non-empty lines as items
        if not items:
            for line in section_text.strip().split("\n"):
                line = line.strip()
                if line and not line.startswith(("#", "*", "---")):
                    items.append(line)

        sections.append(Section(
            kind=kind,
            title=title,
            start=start,
            end=section_end,
            items=items,
        ))

    return sections


# ---------------------------------------------------------------------------
# Family matching — word class membership, not exact phrases
# ---------------------------------------------------------------------------

def match_families(text: str) -> dict[str, list[str]]:
    """Find which lexical families are represented in the text. Pure.

    Returns {family_name: [matched_words]}.
    """
    words = set(re.findall(r'\b\w+\b', text.lower()))
    # Also check 2-word phrases
    lower = text.lower()

    matches: dict[str, list[str]] = {}
    for family_name, family_words in ALL_FAMILIES.items():
        found = []
        for word in family_words:
            if " " in word:
                # Multi-word: substring check
                if word in lower:
                    found.append(word)
            else:
                if word in words:
                    found.append(word)
        if found:
            matches[family_name] = found

    return matches


# ---------------------------------------------------------------------------
# Review perception — the main entry point
# ---------------------------------------------------------------------------

@dataclass
class ReviewFinding:
    """A structured finding from review perception."""
    kind: str           # concern, ownership_gap, action_item, quality_note, risk
    text: str           # the finding text
    section: str        # which section it came from
    severity: str       # p1, p2, p3, or ""
    families: list[str] # which lexical families matched
    confidence: float   # structural + lexical confidence


def perceive_review(text: str) -> list[ReviewFinding]:
    """Perceive a review/spec/critique and extract structured findings. Pure.

    Uses section structure + lexical families + severity markers.
    No phrase matching — pattern recognition over word classes.
    """
    findings: list[ReviewFinding] = []

    sections = parse_sections(text)

    if not sections:
        # No sections found — try whole-text family matching
        families = match_families(text)
        if families:
            # There are relevant word families but no structure
            findings.append(ReviewFinding(
                kind="concern",
                text=text[:200].strip(),
                section="unstructured",
                severity="",
                families=list(families.keys()),
                confidence=0.3,
            ))
        return findings

    for section in sections:
        for item in section.items:
            # Check severity markers
            severity = ""
            sev_match = _SEVERITY_RE.search(item)
            if sev_match:
                severity = sev_match.group(1).lower()

            # Match lexical families
            families = match_families(item)
            family_names = list(families.keys())

            # Compute confidence from structural + lexical signals
            confidence = _compute_confidence(section.kind, severity, family_names)

            # Skip low-confidence items
            if confidence < 0.3:
                continue

            # Determine finding kind from section + families
            kind = _determine_kind(section.kind, family_names, item)

            # Clean the text
            clean = _clean_item(item)
            if len(clean) < 10:
                continue

            findings.append(ReviewFinding(
                kind=kind,
                text=clean,
                section=section.kind,
                severity=severity,
                families=family_names,
                confidence=confidence,
            ))

    return findings


def _compute_confidence(section_kind: str, severity: str, families: list[str]) -> float:
    """Compute confidence from structural + lexical signals. Pure."""
    conf = 0.0

    # Section kind prior
    section_priors = {
        "findings": 0.5,
        "risks": 0.5,
        "recommendations": 0.4,
        "questions": 0.4,
        "overall": 0.3,
        "positive": 0.3,
        "assumptions": 0.2,
        "unknown": 0.1,
    }
    conf += section_priors.get(section_kind, 0.1)

    # Severity marker boost
    if severity:
        conf += 0.3

    # Lexical family boost
    conf += min(len(families) * 0.1, 0.3)

    return min(conf, 1.0)


def _determine_kind(section_kind: str, families: list[str], text: str) -> str:
    """Determine finding kind from section + families. Pure."""
    if "ownership" in families:
        return "ownership_gap"
    if section_kind == "recommendations":
        return "action_item"
    if section_kind == "questions":
        return "open_question"
    if "risk" in families:
        return "concern"
    if "action" in families:
        return "action_item"
    if "quality" in families:
        return "quality_note"
    if section_kind == "findings":
        return "concern"
    if section_kind == "risks":
        return "concern"
    if section_kind == "overall":
        return "assessment"
    return "concern"


def _clean_item(text: str) -> str:
    """Clean a review item for display. Pure."""
    # Strip severity markers
    text = _SEVERITY_RE.sub("", text).strip()
    # Strip leading markdown
    text = re.sub(r"^\*\*|\*\*$", "", text).strip()
    # Strip file path references in brackets
    text = re.sub(r"\[[\w/.:-]+\]\([^)]+\)", "", text).strip()
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Truncate
    if len(text) > 200:
        for sep in (". ", "; ", ", "):
            idx = text.find(sep, 80)
            if 80 < idx < 180:
                text = text[:idx + 1]
                break
        else:
            text = text[:180] + "..."
    return text


# ---------------------------------------------------------------------------
# Bridge to atlas primitives
# ---------------------------------------------------------------------------

def review_findings_to_claims(findings: list[ReviewFinding]) -> list[dict[str, Any]]:
    """Convert review findings to sieve-compatible claims. Pure."""
    claims = []
    for f in findings:
        # Map finding kind to mother type
        mother_type = {
            "concern": "CONSTRAINT",
            "ownership_gap": "CONSTRAINT",
            "action_item": "CONSTRAINT",
            "open_question": "UNCERTAINTY",
            "quality_note": "RELATION",
            "assessment": "RELATION",
            "risk": "CONSTRAINT",
        }.get(f.kind, "RELATION")

        # Map to epistemic event
        event = {
            "concern": "tension_detected",
            "ownership_gap": "tension_detected",
            "action_item": "tension_detected",
            "open_question": "question_posed",
            "quality_note": "belief_formed",
            "assessment": "belief_formed",
            "risk": "tension_detected",
        }.get(f.kind, "belief_formed")

        claims.append({
            "text": f.text,
            "mother_type": mother_type,
            "epistemic_event": event,
            "confidence": f.confidence,
            "claim_type": "constraint" if mother_type == "CONSTRAINT" else "observation",
            "subtype": f.kind,
            "_review_section": f.section,
            "_review_severity": f.severity,
            "_review_families": f.families,
        })

    return claims
