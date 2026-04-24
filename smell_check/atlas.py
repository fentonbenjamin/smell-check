"""Atlas v0 — the named structure behind the chamber's judgment.

Primitives → Laws → Motifs → Coagulators → Judgments → Render

This module defines the data objects. The pipeline consumes them.
Laws are data, not code. Motifs are recipes, not regex.

Pure. No I/O. No model calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Primitives — small typed facts with relational structure
# ---------------------------------------------------------------------------

@dataclass
class Primitive:
    """A single extracted fact from the input."""
    kind: str           # claim, question, challenge, agreement, estimate, dependency
    text: str           # the core clause
    speaker: str = ""
    span: tuple[int, int] | None = None
    clause_id: str = ""
    stance: str = ""          # positive, negative, neutral, hedged
    subject_ref: str = ""     # what this is about
    target_ref: str = ""      # what it targets (e.g., a prior decision)
    supersedes: str = ""      # clause_id of what this replaces
    depends_on: str = ""      # clause_id of a dependency
    evidence_ref: str = ""    # clause_id of supporting evidence
    confidence: float = 0.0
    mother_type: str = ""
    epistemic_event: str = ""


# ---------------------------------------------------------------------------
# Laws — precedence, admissibility, settlement, suppression
# ---------------------------------------------------------------------------

@dataclass
class Law:
    """A named governance rule. Data, not code."""
    name: str
    description: str
    when: str           # trigger condition (human-readable)
    then: str           # consequence (human-readable)
    precedence: int = 0  # higher = applied first


# The decision-state law table
DECISION_LAWS: list[Law] = [
    Law(
        name="challenge_beats_agreement",
        description="A challenge overrides prior agreement unless explicitly resolved later.",
        when="challenge primitive exists for a subject with prior agreement",
        then="subject state = open, not stable",
        precedence=100,
    ),
    Law(
        name="hedge_downgrades_stability",
        description="Hedged language prevents a proposition from being marked stable.",
        when="agreement primitive has stance=hedged",
        then="subject state = provisional, not stable",
        precedence=90,
    ),
    Law(
        name="reversal_supersedes_prior",
        description="A reversed decision supersedes the earlier stable point.",
        when="primitive with supersedes pointing to a prior agreement",
        then="prior agreement removed from stable, new state = open",
        precedence=95,
    ),
    Law(
        name="resolution_settles_challenge",
        description="An explicit resolution after a challenge produces a stable point.",
        when="challenge followed by agreement on the same subject with no further challenge",
        then="subject state = resolved",
        precedence=80,
    ),
    Law(
        name="unresolved_challenge_emits_open_question",
        description="A challenge with no subsequent resolution becomes an open question.",
        when="challenge primitive with no later resolution on the same subject",
        then="emit OpenQuestion judgment",
        precedence=70,
    ),
    Law(
        name="meta_not_stable",
        description="Process/methodology commentary does not become a stable point.",
        when="primitive is meta/process/commentary without concrete decision content",
        then="suppress from stable points",
        precedence=60,
    ),
]

# Index laws by name for lookup
LAW_INDEX: dict[str, Law] = {law.name: law for law in DECISION_LAWS}


# ---------------------------------------------------------------------------
# Motifs — reusable judgment recipes
# ---------------------------------------------------------------------------

@dataclass
class Motif:
    """A named pattern that maps primitives to judgments."""
    name: str
    description: str
    trigger_kinds: list[str]        # primitive kinds that activate this motif
    trigger_events: list[str]       # epistemic events that activate
    blocker_kinds: list[str] = field(default_factory=list)  # kinds that suppress
    blocker_events: list[str] = field(default_factory=list)
    required_laws: list[str] = field(default_factory=list)  # law names to apply
    output_type: str = ""           # judgment kind this motif produces
    merge_key: str = "subject_ref"  # field to group on during coagulation
    examples: list[str] = field(default_factory=list)
    anti_examples: list[str] = field(default_factory=list)


# Decision-state motif family v0
DECISION_MOTIFS: list[Motif] = [
    Motif(
        name="explicit_agreement",
        description="All parties agree, no active challenge.",
        trigger_kinds=["agreement"],
        trigger_events=["belief_formed", "tension_resolved"],
        blocker_kinds=["challenge"],
        blocker_events=["belief_revised"],
        required_laws=["hedge_downgrades_stability"],
        output_type="StablePoint",
        examples=["Agreed. Let's go with that.", "Sounds good. I'll have the PR up."],
        anti_examples=["Yeah, probably.", "I guess so."],
    ),
    Motif(
        name="challenge",
        description="Someone questions or pushes back on a prior position.",
        trigger_kinds=["challenge", "question"],
        trigger_events=["belief_revised", "question_posed"],
        blocker_kinds=[],
        blocker_events=[],
        required_laws=["challenge_beats_agreement", "unresolved_challenge_emits_open_question"],
        output_type="OpenQuestion",
        examples=["Wait, did anyone evaluate vendor B?", "I thought we already decided on MySQL?"],
        anti_examples=["What time is the meeting?"],
    ),
    Motif(
        name="challenge_then_resolved",
        description="A challenge followed by explicit resolution.",
        trigger_kinds=["challenge", "agreement"],
        trigger_events=["belief_revised", "tension_resolved"],
        blocker_kinds=[],
        blocker_events=[],
        required_laws=["resolution_settles_challenge"],
        output_type="ResolvedDecision",
        examples=["That won't work. → Good point, what about X? → That could work. Confirmed."],
        anti_examples=["That won't work. → Fine, but I'm not happy about it."],
    ),
    Motif(
        name="decision_reversal",
        description="A prior decision is being overturned.",
        trigger_kinds=["challenge"],
        trigger_events=["belief_revised"],
        blocker_kinds=[],
        blocker_events=["tension_resolved"],
        required_laws=["reversal_supersedes_prior", "unresolved_challenge_emits_open_question"],
        output_type="OpenQuestion",
        examples=["We should switch to Postgres. → But we already decided on MySQL?"],
        anti_examples=[],
    ),
    Motif(
        name="hedged_agreement",
        description="Apparent agreement with uncertainty markers.",
        trigger_kinds=["agreement"],
        trigger_events=["belief_formed"],
        blocker_kinds=[],
        blocker_events=[],
        required_laws=["hedge_downgrades_stability"],
        output_type="ProvisionalDecision",
        examples=["Yeah, probably.", "I guess. Let me know.", "Sure, when you get a chance."],
        anti_examples=["Agreed. Let's do it.", "Perfect. I'll start tomorrow."],
    ),
]

MOTIF_INDEX: dict[str, Motif] = {m.name: m for m in DECISION_MOTIFS}


# ---------------------------------------------------------------------------
# Contrast Pairs — near-neighbors the system must distinguish
# ---------------------------------------------------------------------------

@dataclass
class ContrastPair:
    """Two motifs that look similar but have different judgment outcomes."""
    name: str
    motif_a: str       # motif name
    motif_b: str       # motif name
    distinguisher: str  # what tells them apart
    fixture_a: str = ""  # fixture_id for motif_a
    fixture_b: str = ""  # fixture_id for motif_b


CONTRAST_PAIRS: list[ContrastPair] = [
    ContrastPair(
        name="agreement_vs_hedged",
        motif_a="explicit_agreement",
        motif_b="hedged_agreement",
        distinguisher="Hedge words (probably, guess, maybe) downgrade agreement to provisional.",
        fixture_a="explicit_agreement",
        fixture_b="hedged_agreement",
    ),
    ContrastPair(
        name="challenge_open_vs_resolved",
        motif_a="challenge",
        motif_b="challenge_then_resolved",
        distinguisher="Explicit resolution after the challenge settles it. Without resolution, stays open.",
        fixture_a="challenged_stays_open",
        fixture_b="challenged_then_resolved",
    ),
    ContrastPair(
        name="clean_vs_reversed",
        motif_a="explicit_agreement",
        motif_b="decision_reversal",
        distinguisher="A reversal contains a challenge to a prior decision. Clean agreement has no prior to challenge.",
        fixture_a="clean_agreement",
        fixture_b="db_decision_reversal",
    ),
]


# ---------------------------------------------------------------------------
# Judgments — structured outputs, not text fragments
# ---------------------------------------------------------------------------

@dataclass
class Judgment:
    """A structured judgment object. The seam between analysis and rendering."""
    kind: str           # StablePoint, OpenQuestion, Concern, ProvisionalDecision, etc.
    subject: str        # what this judgment is about (normalized)
    state: str          # stable, open, provisional, resolved, contested
    why: str            # one-sentence reason
    anchors: list[dict[str, Any]] = field(default_factory=list)  # source spans
    blockers: list[str] = field(default_factory=list)             # what prevents settlement
    next_step: str = ""  # what to do about it
    laws_applied: list[str] = field(default_factory=list)         # which laws produced this
    motif: str = ""      # which motif matched
    evidence: list[str] = field(default_factory=list)             # supporting text fragments
    confidence_basis: str = ""  # what the confidence comes from (not a float)


# ---------------------------------------------------------------------------
# Decision Coagulator — the first real coagulator
# ---------------------------------------------------------------------------

_HEDGE_WORDS = frozenset({
    "probably", "maybe", "perhaps", "possibly", "might",
    "could", "i guess", "i suppose", "not sure", "when you get a chance",
    "yeah, probably", "sure,",
})

_AGREEMENT_CUES = frozenset({
    "agreed", "sounds good", "makes sense", "perfect",
    "let's go with", "let's do", "will do", "confirmed",
    "we decided", "we agreed", "decided to", "the plan is",
    "locked in", "set for",
})

_CHALLENGE_CUES = frozenset({
    "wait,", "hold on", "but we", "i thought we",
    "didn't we", "wasn't that", "did anyone",
})


def coagulate_decisions(
    primitives: list[Primitive],
) -> list[Judgment]:
    """Decision coagulator: merge primitives into decision-state judgments.

    Groups by subject_ref (or text similarity when subject_ref is empty).
    Applies laws in precedence order to determine final state.
    Emits one judgment per decision subject.

    Pure function.
    """
    if not primitives:
        return []

    # Classify each primitive's decision role
    agreements: list[Primitive] = []
    challenges: list[Primitive] = []
    questions: list[Primitive] = []
    resolutions: list[Primitive] = []
    hedged: list[Primitive] = []

    for p in primitives:
        lower = p.text.lower()

        # Resolution signals
        if p.epistemic_event == "tension_resolved" or any(
            cue in lower for cue in ("confirmed", "let's go with", "perfect")
        ):
            resolutions.append(p)
            continue

        # Challenge signals
        if p.kind == "challenge" or p.epistemic_event == "belief_revised" or (
            "?" in p.text and any(cue in lower for cue in _CHALLENGE_CUES)
        ):
            challenges.append(p)
            continue

        # Question signals (explicit questions, not challenges)
        if p.kind == "question" or p.epistemic_event == "question_posed":
            questions.append(p)
            continue

        # Hedged agreement
        if any(hw in lower for hw in _HEDGE_WORDS):
            hedged.append(p)
            continue

        # Clean agreement
        if any(cue in lower for cue in _AGREEMENT_CUES) or (
            p.epistemic_event == "belief_formed" and p.stance == "positive"
        ):
            agreements.append(p)
            continue

        # Default: treat as a claim (mild agreement)
        if p.mother_type == "CONTRACT":
            agreements.append(p)
        elif p.mother_type == "CONSTRAINT":
            # Constraints go to concerns, handled elsewhere
            pass

    # Apply laws in precedence order to determine overall decision state
    judgments: list[Judgment] = []

    # Law: challenge_beats_agreement (precedence 100)
    if challenges and not resolutions:
        # Unresolved challenge → open question
        best_challenge = max(challenges, key=lambda p: p.confidence)
        judgments.append(Judgment(
            kind="OpenQuestion",
            subject=_normalize(best_challenge.text),
            state="open",
            why="A challenge or question was raised and not explicitly resolved.",
            anchors=[_anchor(p) for p in challenges],
            blockers=["No explicit resolution found."],
            next_step="Resolve the challenge before treating this as decided.",
            laws_applied=["challenge_beats_agreement", "unresolved_challenge_emits_open_question"],
            motif="challenge" if len(challenges) == 1 else "decision_reversal",
            evidence=[p.text for p in challenges],
        ))

    # Law: resolution_settles_challenge (precedence 80)
    if challenges and resolutions:
        best_resolution = max(resolutions, key=lambda p: p.confidence)
        judgments.append(Judgment(
            kind="ResolvedDecision",
            subject=_normalize(best_resolution.text),
            state="resolved",
            why="A challenge was raised and then explicitly resolved.",
            anchors=[_anchor(p) for p in resolutions + challenges],
            next_step="",
            laws_applied=["resolution_settles_challenge"],
            motif="challenge_then_resolved",
            evidence=[p.text for p in challenges + resolutions],
        ))

    # Law: hedge_downgrades_stability (precedence 90)
    if hedged and not challenges:
        best_hedge = max(hedged, key=lambda p: len(p.text))
        judgments.append(Judgment(
            kind="ProvisionalDecision",
            subject=_normalize(best_hedge.text),
            state="provisional",
            why="Agreement language is present but hedged — not fully committed.",
            anchors=[_anchor(p) for p in hedged],
            blockers=["Hedging language suggests incomplete commitment."],
            next_step="Confirm explicitly or acknowledge the uncertainty.",
            laws_applied=["hedge_downgrades_stability"],
            motif="hedged_agreement",
            evidence=[p.text for p in hedged],
        ))

    # Clean agreement (only if no challenges or hedges)
    if agreements and not challenges and not hedged:
        best_agreement = max(agreements, key=lambda p: len(p.text))
        judgments.append(Judgment(
            kind="StablePoint",
            subject=_normalize(best_agreement.text),
            state="stable",
            why="Explicit agreement with no active challenge.",
            anchors=[_anchor(p) for p in agreements],
            next_step="",
            laws_applied=[],
            motif="explicit_agreement",
            evidence=[p.text for p in agreements],
        ))

    # Standalone questions (not challenges to prior decisions)
    for q in questions:
        judgments.append(Judgment(
            kind="OpenQuestion",
            subject=_normalize(q.text),
            state="open",
            why="Explicitly raised as a question.",
            anchors=[_anchor(q)],
            next_step="Answer or resolve.",
            laws_applied=[],
            motif="",
            evidence=[q.text],
        ))

    return judgments


# ---------------------------------------------------------------------------
# Primitive extraction — bridge from current pipeline to atlas primitives
# ---------------------------------------------------------------------------

def claims_to_primitives(claims: list[dict[str, Any]]) -> list[Primitive]:
    """Convert promoted/contested/deferred claims into atlas primitives. Pure."""
    primitives = []
    for c in claims:
        text = c.get("text", "")
        lower = text.lower()
        mother_type = c.get("mother_type", "")
        event = c.get("epistemic_event", "")

        # Determine kind
        if mother_type == "UNCERTAINTY" or event == "question_posed":
            kind = "question"
        elif event == "belief_revised" or any(
            cue in lower for cue in ("wait,", "hold on", "i thought we", "didn't we")
        ):
            kind = "challenge"
        elif any(cue in lower for cue in _AGREEMENT_CUES):
            kind = "agreement"
        elif mother_type == "CONSTRAINT":
            kind = "dependency"
        elif mother_type == "WITNESS":
            kind = "claim"
        else:
            kind = "claim"

        # Determine stance
        stance = "neutral"
        if any(hw in lower for hw in _HEDGE_WORDS):
            stance = "hedged"
        elif any(cue in lower for cue in _AGREEMENT_CUES):
            stance = "positive"
        elif "?" in text or kind == "challenge":
            stance = "negative"

        primitives.append(Primitive(
            kind=kind,
            text=text,
            speaker=_extract_speaker(text),
            clause_id=c.get("clause_id", ""),
            span=tuple(c.get("source_span")) if c.get("source_span") else None,
            stance=stance,
            confidence=c.get("confidence", 0.0) or 0.0,
            mother_type=mother_type,
            epistemic_event=event,
        ))

    return primitives


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import re

_SPEAKER_RE = re.compile(r"^[A-Z][A-Za-z\s]*:\s*")

def _normalize(text: str) -> str:
    """Strip speaker attribution and trim for judgment subject."""
    t = _SPEAKER_RE.sub("", text).strip()
    if len(t) > 100:
        for sep in (". ", "; ", ", "):
            idx = t.find(sep, 30)
            if 30 < idx < 80:
                t = t[:idx]
                break
        else:
            t = t[:80] + "..."
    return t


def _extract_speaker(text: str) -> str:
    """Extract speaker name from 'Speaker: text' format."""
    m = _SPEAKER_RE.match(text)
    if m:
        return m.group().rstrip(": ").strip()
    return ""


def _anchor(p: Primitive) -> dict[str, Any]:
    """Convert a primitive to a source anchor dict."""
    a: dict[str, Any] = {"text": p.text, "clause_id": p.clause_id}
    if p.span:
        a["char_offset"] = list(p.span)
    return a
