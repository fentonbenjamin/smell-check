"""Atlas v0 — the named structure behind the chamber's judgment.

Primitives → Laws → Motifs → Coagulators → Judgments → Render

This module defines the data objects. The pipeline consumes them.
Laws are data, not code. Motifs are recipes, not regex.

Pure. No I/O. No model calls.
"""

from __future__ import annotations

import re
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
    emit_per_primitive: bool = False  # if True, emit one judgment per primitive instead of coagulating
    examples: list[str] = field(default_factory=list)
    anti_examples: list[str] = field(default_factory=list)


# Decision-state motif family v0
DECISION_MOTIFS: list[Motif] = [
    Motif(
        name="explicit_agreement",
        description="All parties agree, no active challenge, no hedging.",
        trigger_kinds=["agreement"],
        trigger_events=["belief_formed", "tension_resolved"],
        blocker_kinds=["challenge"],
        blocker_events=["belief_revised", "commitment_hedged"],
        required_laws=["hedge_downgrades_stability"],
        output_type="StablePoint",
        examples=["Agreed. Let's go with that.", "Sounds good. I'll have the PR up."],
        anti_examples=["Yeah, probably.", "I guess so."],
    ),
    Motif(
        name="challenge",
        description="Someone questions or pushes back on a prior position.",
        trigger_kinds=["challenge"],
        trigger_events=["belief_revised"],
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
        anti_examples=["What time is standup tomorrow?", "Can you review my PR?"],
    ),
    Motif(
        name="hedged_agreement",
        description="Apparent agreement with uncertainty markers.",
        trigger_kinds=["agreement"],
        trigger_events=["belief_formed"],
        blocker_kinds=[],
        blocker_events=[],
        required_laws=["hedge_downgrades_stability", "meta_not_stable"],
        output_type="ProvisionalDecision",
        examples=["Yeah, probably.", "I guess. Let me know.", "Sure, when you get a chance."],
        anti_examples=["Agreed. Let's do it.", "Perfect. I'll start tomorrow."],
    ),
]


# ---------------------------------------------------------------------------
# Operational readiness motifs
# ---------------------------------------------------------------------------

_OWNERSHIP_CUES = (
    "who's going to", "who will", "who owns", "can someone",
    "someone needs to", "someone has to", "who's responsible",
    "need someone to", "anybody", "anyone",
)

_EVIDENCE_CHALLENGE_CUES = (
    "not a real test", "that's not", "that won't work",
    "doesn't prove", "doesn't show", "not representative",
    "not production", "not at scale", "1% of", "only staging",
)

_DEFERRAL_CUES = (
    "figure it out later", "deal with that when",
    "closer to the date", "when it comes up", "if it becomes",
    "we'll see", "cross that bridge", "worry about it later",
    "not now", "later",
)

OPERATIONAL_LAWS: list[Law] = [
    Law(
        name="ownership_gap_is_risk",
        description="An action without a clear owner is a risk, not a requirement.",
        when="question about ownership with no answer in the thread",
        then="emit OwnershipGap, not Requirement",
        precedence=75,
    ),
    Law(
        name="evidence_challenge_is_concern",
        description="Challenging the quality of evidence is a concern about validity.",
        when="statement challenges test coverage, data quality, or proof",
        then="emit EvidenceChallenge, not Requirement",
        precedence=75,
    ),
    Law(
        name="unresolved_dependency_blocks_go_ahead",
        description="Unresolved operational dependencies block a confident go-ahead.",
        when="multiple operational concerns exist without resolution",
        then="emit one Concern with all blockers",
        precedence=65,
    ),
    Law(
        name="deferral_is_risk",
        description="Deferring a dependency is a risk signal, not resolution.",
        when="response to a concern is 'we'll deal with it later'",
        then="emit Concern with deferral blocker",
        precedence=70,
    ),
]

OPERATIONAL_MOTIFS: list[Motif] = [
    Motif(
        name="ownership_gap",
        description="An action or responsibility has no clear owner.",
        trigger_kinds=["dependency"],
        trigger_events=["tension_detected"],
        blocker_kinds=[],
        blocker_events=[],
        required_laws=["ownership_gap_is_risk", "unresolved_dependency_blocks_go_ahead"],
        output_type="OpenQuestion",
        examples=["Who's going to be on call?", "Can someone own the runbook?"],
        anti_examples=["I'll handle it.", "Dev B is on point for this."],
    ),
    Motif(
        name="evidence_challenge",
        description="Someone challenges the quality or validity of evidence.",
        trigger_kinds=["challenge", "dependency"],
        trigger_events=["tension_detected", "belief_revised"],
        blocker_kinds=["resolution"],
        blocker_events=["tension_resolved"],
        required_laws=["evidence_challenge_is_concern"],
        output_type="Concern",
        examples=["Staging is 1% of prod. That's not a real test.", "We haven't tested on production data."],
        anti_examples=["The staging run looked clean.", "Tests pass."],
    ),
    Motif(
        name="operational_requirement",
        description="A concrete prerequisite, deadline, or action constraint.",
        trigger_kinds=["dependency"],
        trigger_events=["tension_detected"],
        blocker_kinds=[],
        blocker_events=[],
        required_laws=["deferral_is_risk"],
        output_type="Concern",
        emit_per_primitive=True,  # each requirement is independent
        examples=["We need the runbook before go-live.", "We need to decide by Friday."],
        anti_examples=["It would be nice to have.", "Maybe we should consider."],
    ),
]

# Combine all motifs and laws
ALL_MOTIFS = DECISION_MOTIFS + OPERATIONAL_MOTIFS
ALL_LAWS = DECISION_LAWS + OPERATIONAL_LAWS
MOTIF_INDEX: dict[str, Motif] = {m.name: m for m in ALL_MOTIFS}
LAW_INDEX.update({law.name: law for law in OPERATIONAL_LAWS})


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
    motifs: list[Motif] | None = None,
    laws: list[Law] | None = None,
) -> list[Judgment]:
    """Decision coagulator: match motifs, apply laws, emit judgments.

    Pipeline:
      1. Group primitives by kind (using the kind from claims_to_primitives)
      2. Match motifs in precedence order against primitive kinds
      3. Check blocker conditions
      4. Apply required laws from the matched motif
      5. Consume matched primitives (no double-matching)
      6. Emit one structured judgment per matched motif

    Pure function.
    """
    if not primitives:
        return []

    motifs = motifs or ALL_MOTIFS
    laws = laws or ALL_LAWS
    law_by_name = {law.name: law for law in laws}

    # --- Step 0: extract the governing subject ---
    governing_subject = _extract_governing_subject(primitives)

    # --- Step 1: group primitives by kind ---
    by_kind: dict[str, list[Primitive]] = {}
    for p in primitives:
        by_kind.setdefault(p.kind, []).append(p)

    # Track which primitives have been consumed by a motif
    consumed: set[str] = set()  # clause_ids

    def _is_consumed(p: Primitive) -> bool:
        return p.clause_id in consumed if p.clause_id else id(p) in consumed

    def _consume(ps: list[Primitive]) -> None:
        for p in ps:
            consumed.add(p.clause_id if p.clause_id else id(p))

    # All primitive kinds present (for blocker checks)
    all_kinds = set(by_kind.keys())
    all_events = {p.epistemic_event for p in primitives if p.epistemic_event}

    # --- Step 2-5: match motifs in precedence order ---
    def _motif_precedence(m: Motif) -> int:
        if not m.required_laws:
            return 0
        return max(
            (law_by_name[ln].precedence for ln in m.required_laws if ln in law_by_name),
            default=0,
        )

    sorted_motifs = sorted(motifs, key=_motif_precedence, reverse=True)

    judgments: list[Judgment] = []

    for motif in sorted_motifs:
        # Find unconsumed primitives matching trigger conditions
        triggered_by: list[Primitive] = []
        for p in primitives:
            if _is_consumed(p):
                continue
            if p.kind in motif.trigger_kinds or p.epistemic_event in motif.trigger_events:
                # Operational motifs need text-level confirmation
                # to avoid consuming all dependencies indiscriminately
                if motif.name == "ownership_gap":
                    if not any(cue in p.text.lower() for cue in _OWNERSHIP_CUES):
                        continue
                elif motif.name == "evidence_challenge":
                    if not any(cue in p.text.lower() for cue in _EVIDENCE_CHALLENGE_CUES):
                        continue
                triggered_by.append(p)

        if not triggered_by:
            continue

        # Check blockers against ALL primitives (not just unconsumed)
        blocked = bool(
            (set(motif.blocker_kinds) & all_kinds)
            or (set(motif.blocker_events) & all_events)
        )
        if blocked:
            continue

        # Motif matched — collect required laws
        applied_laws = [ln for ln in motif.required_laws if ln in law_by_name]

        # Pick the best representative — highest confidence, then shortest
        # (shorter = more specific, avoids full-input fallback text)
        best = max(triggered_by, key=lambda p: (p.confidence, -len(p.text)))

        if motif.emit_per_primitive:
            # Emit one judgment per primitive (independent concerns)
            # Use the primitive's own text as subject, not the governing subject
            for p in triggered_by:
                composed = _normalize(p.text)
                judgments.append(Judgment(
                    kind=motif.output_type,
                    subject=composed,
                    state=_state_from_kind(motif.output_type),
                    why=motif.description,
                    anchors=[_anchor(p)],
                    blockers=_blockers_for_motif(motif, by_kind),
                    next_step=_next_step_for(motif.output_type),
                    laws_applied=applied_laws,
                    motif=motif.name,
                    evidence=[p.text],
                ))
        else:
            # Coagulate into one judgment
            best = max(triggered_by, key=lambda p: (p.confidence, -len(p.text)))
            composed = _compose_judgment(
                motif.output_type, governing_subject, best.text,
                blocker_text=best.text if motif.output_type == "OpenQuestion" else "",
            )
            judgments.append(Judgment(
                kind=motif.output_type,
                subject=composed,
                state=_state_from_kind(motif.output_type),
                why=motif.description,
                anchors=[_anchor(p) for p in triggered_by],
                blockers=_blockers_for_motif(motif, by_kind),
                next_step=_next_step_for(motif.output_type),
                laws_applied=applied_laws,
                motif=motif.name,
                evidence=[p.text for p in triggered_by],
                confidence_basis=f"trigger: {len(triggered_by)} primitives, laws: {len(applied_laws)}",
            ))

        # Consume the matched primitives
        _consume(triggered_by)

    # --- Unconsumed questions → standalone open questions ---
    for p in by_kind.get("question", []):
        if _is_consumed(p):
            continue
        composed = _compose_open_question(governing_subject, p.text)
        judgments.append(Judgment(
            kind="OpenQuestion",
            subject=composed,
            state="open",
            why="Explicitly raised as a question.",
            anchors=[_anchor(p)],
            next_step="Answer or resolve.",
            laws_applied=[],
            motif="",
            evidence=[p.text],
        ))
        _consume([p])

    # --- Unconsumed claims → weak stable points (only if no motifs matched) ---
    unconsumed_claims = [p for p in by_kind.get("claim", []) if not _is_consumed(p)]
    if unconsumed_claims and not judgments:
        best = max(unconsumed_claims, key=lambda p: len(p.text))
        if len(best.text) > 40:
            judgments.append(Judgment(
                kind="StablePoint",
                subject=_normalize(best.text),
                state="stable",
                why="Stated as a factual claim with no active challenge.",
                anchors=[_anchor(p) for p in unconsumed_claims],
                laws_applied=[],
                motif="",
                evidence=[p.text for p in unconsumed_claims],
            ))

    # --- Subject dedup: one dominant state per subject ---
    # If two judgments have high word overlap in their subjects,
    # the one with higher-precedence laws wins. This prevents
    # the same subject from appearing as both tentative and resolved.
    judgments = _dedup_by_subject(judgments, law_by_name)

    return judgments


# ---------------------------------------------------------------------------
# Concern Coagulator — merges operational signals into readiness concerns
# ---------------------------------------------------------------------------

_OPERATIONAL_MOTIFS = frozenset({"ownership_gap", "evidence_challenge", "operational_requirement"})


def coagulate_concerns(
    judgments: list[Judgment],
    governing_subject: str = "",
) -> list[Judgment]:
    """Concern coagulator: merge operational judgments into readiness concerns.

    Takes the output of coagulate_decisions and:
    1. Groups operational judgments (ownership_gap, evidence_challenge,
       operational_requirement) into readiness clusters
    2. Emits one Concern per cluster with supporting evidence
    3. Passes through non-operational judgments unchanged

    Law: unresolved operational dependency blocks confident go-ahead.
    Evidence fragments become support, not separate top-level findings.

    Pure function.
    """
    # Separate operational vs non-operational judgments
    operational: list[Judgment] = []
    passthrough: list[Judgment] = []

    for j in judgments:
        if j.motif in _OPERATIONAL_MOTIFS:
            operational.append(j)
        else:
            passthrough.append(j)

    if len(operational) < 3:
        # Too few signals to coagulate — keep as independent findings
        return judgments

    # Collect all blockers and evidence from operational judgments
    all_blockers: list[str] = []
    all_evidence: list[str] = []
    all_anchors: list[dict[str, Any]] = []
    all_laws: list[str] = []
    concern_parts: list[str] = []
    open_questions: list[Judgment] = []

    for j in operational:
        # Operational requirements stay as individual items in the evidence
        all_evidence.extend(j.evidence)
        all_anchors.extend(j.anchors)
        all_blockers.extend(j.blockers)
        for law in j.laws_applied:
            if law not in all_laws:
                all_laws.append(law)

        # Build a readable concern part from each operational judgment
        if j.motif == "ownership_gap":
            concern_parts.append(f"Ownership unclear: {j.subject}")
            # Also emit as an open question
            open_questions.append(Judgment(
                kind="OpenQuestion",
                subject=j.subject,
                state="open",
                why="Ownership or responsibility is unassigned.",
                anchors=j.anchors,
                next_step="Assign an owner.",
                laws_applied=j.laws_applied,
                motif=j.motif,
                evidence=j.evidence,
            ))
        elif j.motif == "evidence_challenge":
            concern_parts.append(f"Evidence challenged: {j.subject}")
        elif j.motif == "operational_requirement":
            concern_parts.append(j.subject)

    # Compose the concern
    if governing_subject:
        concern_subject = f"{governing_subject[0].upper()}{governing_subject[1:]} has operational readiness gaps"
    elif concern_parts:
        concern_subject = "Operational readiness has gaps"
    else:
        concern_subject = "Unresolved operational concerns"

    # Build the why from the parts
    if len(concern_parts) <= 3:
        why = ". ".join(concern_parts) + "."
    else:
        why = f"{len(concern_parts)} operational issues: {'. '.join(concern_parts[:2])}. And {len(concern_parts) - 2} more."

    # The main concern
    concern = Judgment(
        kind="Concern",
        subject=concern_subject,
        state="active",
        why=why,
        anchors=all_anchors,
        blockers=all_blockers or ["Unresolved operational dependencies."],
        next_step="Resolve operational gaps before proceeding with confidence.",
        laws_applied=all_laws + ["unresolved_dependency_blocks_go_ahead"],
        motif="concern_v0",
        evidence=all_evidence,
    )

    return passthrough + [concern] + open_questions


def _dedup_by_subject(
    judgments: list[Judgment],
    law_by_name: dict[str, Law],
) -> list[Judgment]:
    """Remove conflicting judgments about the same subject. Pure.

    When two judgments share >40% word overlap in their subjects,
    keep the one whose laws have higher max precedence.
    """
    if len(judgments) <= 1:
        return judgments

    import string as _string
    _stop = frozenset({"a", "an", "the", "is", "are", "was", "were", "to", "for",
                        "of", "in", "on", "and", "or", "but", "not", "with", "has",
                        "have", "it", "we", "i", "that", "this", "only"})

    def _words(text: str) -> set[str]:
        stripped = text.lower().translate(str.maketrans("", "", _string.punctuation))
        return set(stripped.split()) - _stop

    def _max_precedence(j: Judgment) -> int:
        if not j.laws_applied:
            return 0
        return max(
            (law_by_name[ln].precedence for ln in j.laws_applied if ln in law_by_name),
            default=0,
        )

    # Build keep list — for each pair with high overlap, keep the stronger one
    to_remove: set[int] = set()
    for i in range(len(judgments)):
        if i in to_remove:
            continue
        for j_idx in range(i + 1, len(judgments)):
            if j_idx in to_remove:
                continue
            wi = _words(judgments[i].subject)
            wj = _words(judgments[j_idx].subject)
            if not wi or not wj:
                continue
            overlap = len(wi & wj) / len(wi | wj)
            if overlap > 0.4:
                # Same subject — keep the one with higher precedence
                pi = _max_precedence(judgments[i])
                pj = _max_precedence(judgments[j_idx])
                if pi >= pj:
                    to_remove.add(j_idx)
                else:
                    to_remove.add(i)

    return [j for idx, j in enumerate(judgments) if idx not in to_remove]


# ---------------------------------------------------------------------------
# Subject extraction — what is this conversation about?
# ---------------------------------------------------------------------------

# Proposal/decision verbs that introduce the governing subject
_PROPOSAL_CUES = (
    "we're shipping ", "we're launching ", "we're deploying ", "we're releasing ",
    "we should ", "let's ", "the plan is ", "i propose ", "i think we should ",
    "we're going to ", "we're going with ", "going to ", "we need to ",
    "we want to ", "i want to ", "should we ", "can we ", "why don't we ",
)


def _extract_governing_subject(primitives: list[Primitive]) -> str:
    """Find the governing subject of the conversation. Pure.

    Looks for the first proposal/decision clause and extracts its object.
    Falls back to the longest substantive primitive text.
    """
    # Look for proposal language
    for p in primitives:
        lower = p.text.lower()
        for cue in _PROPOSAL_CUES:
            if cue in lower:
                # Extract the clause after the cue, stop at sentence boundary
                idx = lower.index(cue) + len(cue)
                rest = p.text[idx:].strip()
                # Truncate at first sentence break
                for sep in (".\n", ".\r", ". ", "?\n", "?\r", "? ", "\n"):
                    end = rest.find(sep)
                    if 0 < end < 120:
                        rest = rest[:end]
                        break
                subject = rest.strip().rstrip(".")
                if 10 < len(subject) < 120:
                    return subject

    # Also check ALL primitives (including questions) for proposal language
    # "We're shipping X" might be classified as a question by the tagger
    for p in primitives:
        lower = p.text.lower()
        for cue in _PROPOSAL_CUES:
            if cue in lower:
                idx = lower.index(cue) + len(cue)
                rest = p.text[idx:].strip()
                for sep in (".\n", ".\r", ". ", "?\n", "?\r", "? ", "\n"):
                    end = rest.find(sep)
                    if 0 < end < 120:
                        rest = rest[:end]
                        break
                subject = rest.strip().rstrip(".")
                if 10 < len(subject) < 120:
                    return subject

    # Fallback: first substantive primitive (the opening statement is usually the topic)
    for p in primitives:
        clean = _normalize(p.text)
        if len(clean) > 20:
            return clean

    return ""


def _compose_judgment(kind: str, subject: str, evidence_text: str, blocker_text: str = "") -> str:
    """Compose a subject-first judgment sentence. Pure.

    Instead of quoting the raw span, frame the judgment around the governing subject.
    """
    if not subject:
        # No subject recovered — fall back to normalized evidence
        return _normalize(evidence_text)

    # Capitalize subject for sentence start
    subj = subject[0].upper() + subject[1:] if subject else ""

    if kind == "StablePoint":
        return f"{subj} is agreed"
    elif kind == "ResolvedDecision":
        return f"{subj} was challenged and then explicitly resolved"
    elif kind == "ProvisionalDecision":
        return f"{subj} has only tentative agreement — not fully committed"
    elif kind == "OpenQuestion":
        if blocker_text:
            # Frame as a question about the blocker
            blocker_clean = _normalize(blocker_text)
            return f"Unresolved: {blocker_clean}"
        return f"It is unclear whether {subject} is settled"
    elif kind == "Concern":
        return f"{subj} has unresolved risks"
    else:
        return _normalize(evidence_text)


def _compose_open_question(subject: str, question_text: str) -> str:
    """Compose a subject-aware open question. Pure."""
    clean = _normalize(question_text)
    # If the question already contains a question mark, keep it
    if "?" in clean:
        return clean
    # If we have a subject, frame the question around it
    if subject and len(clean) > 15:
        return f"{clean} — how does this affect {subject}?"
    return clean


def _state_from_kind(kind: str) -> str:
    """Map judgment kind to state."""
    return {
        "StablePoint": "stable",
        "OpenQuestion": "open",
        "ProvisionalDecision": "provisional",
        "ResolvedDecision": "resolved",
        "Concern": "active",
        "ContradictionCluster": "contested",
    }.get(kind, "unknown")


def _next_step_for(kind: str) -> str:
    """Default next step for a judgment kind."""
    return {
        "StablePoint": "",
        "OpenQuestion": "Resolve before relying on it.",
        "ProvisionalDecision": "Confirm explicitly or acknowledge the uncertainty.",
        "ResolvedDecision": "",
        "Concern": "Address the concern.",
    }.get(kind, "")


def _blockers_for_motif(motif: Motif, by_kind: dict[str, list[Primitive]]) -> list[str]:
    """Generate blockers list based on what's missing."""
    blockers = []
    if motif.name in ("challenge", "decision_reversal"):
        if not by_kind.get("resolution"):
            blockers.append("No explicit resolution found.")
    if motif.name == "hedged_agreement":
        blockers.append("Hedging language suggests incomplete commitment.")
    return blockers


# ---------------------------------------------------------------------------
# Primitive extraction — bridge from current pipeline to atlas primitives
# ---------------------------------------------------------------------------

def claims_to_primitives(claims: list[dict[str, Any]]) -> list[Primitive]:
    """Convert promoted/contested/deferred claims into atlas primitives. Pure.

    Classification priority (first match wins):
    1. Resolution — tension_resolved or explicit confirmation cues
    2. Challenge — belief_revised, challenge cues, or questions about prior decisions
    3. Question — question_posed or UNCERTAINTY
    4. Agreement — agreement cues or positive stance
    5. Dependency — CONSTRAINT
    6. Claim — everything else
    """
    primitives = []
    for c in claims:
        text = c.get("text", "")
        lower = text.lower()
        mother_type = c.get("mother_type", "")
        event = c.get("epistemic_event", "")

        # 0. Contested claims are challenges by definition
        if c.get("_contested"):
            kind = "challenge"
            stance = "negative"
        # 1. Resolution (highest priority — settles challenges)
        elif event == "tension_resolved" or any(
            cue in lower for cue in ("confirmed", "let's go with", "perfect")
        ):
            kind = "resolution"
            stance = "positive"
        # 2. Challenge (including evidence challenges)
        elif event == "belief_revised" or any(
            cue in lower for cue in _CHALLENGE_CUES
        ) or any(cue in lower for cue in _EVIDENCE_CHALLENGE_CUES):
            kind = "challenge"
            stance = "negative"
        # 3. Ownership question (question about who owns/does something → dependency)
        elif (mother_type == "UNCERTAINTY" or event == "question_posed") and any(
            cue in lower for cue in _OWNERSHIP_CUES
        ):
            kind = "dependency"
            stance = "negative"
        # 3b. Regular question
        elif mother_type == "UNCERTAINTY" or event == "question_posed":
            kind = "question"
            stance = "negative"
        # 4. Hedged agreement (agreement with reservations)
        elif event == "commitment_hedged" or any(hw in lower for hw in _HEDGE_WORDS):
            kind = "agreement"
            stance = "hedged"
        # 5. Clean agreement
        elif any(cue in lower for cue in _AGREEMENT_CUES):
            kind = "agreement"
            stance = "positive"
        # 6. Dependency (CONSTRAINT)
        elif mother_type == "CONSTRAINT":
            kind = "dependency"
            stance = "neutral"
        # 7. Default claim
        else:
            kind = "claim"
            stance = "neutral"

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


# ---------------------------------------------------------------------------
# Pipeline shape — frozen canonical layer order
# ---------------------------------------------------------------------------
#
# This is the chamber's judgment pipeline. Each layer feeds the next.
# Skipping a layer is a law violation, not an optimization.
#
# ┌──────────────────────────────────────────────────────────────┐
# │  1. tagger          perceive clauses and epistemic events    │
# │  2. mother_types    map events to governance types           │
# │  3. sieve           promote, contest, defer, or lose claims  │
# │  4. primitives      extract typed facts with relational      │
# │                     structure from promoted claims            │
# │  5. motif matching  match primitives against motif triggers  │
# │                     and check blockers                        │
# │  6. law application apply required laws from matched motif   │
# │  7. coagulation     merge motif hits into one-per-concern    │
# │                     judgments, consume matched primitives     │
# │  8. judgments        structured output: kind, subject, state, │
# │                     why, anchors, blockers, next_step        │
# │  9. render           lane-aware surface grammar               │
# └──────────────────────────────────────────────────────────────┘

PIPELINE_LAYERS = (
    "tagger",
    "mother_types",
    "sieve",
    "primitives",
    "motif_matching",
    "law_application",
    "coagulation",
    "judgments",
    "render",
)


# ---------------------------------------------------------------------------
# Growth governance — motif admission + coverage tracking
# ---------------------------------------------------------------------------

def verify_motif_admission(motif: Motif) -> tuple[bool, list[str]]:
    """Verify a motif meets the admission bar. Pure.

    Every motif must have:
    1. At least one positive example
    2. At least one anti-example (counterexample)
    3. Trigger kinds or events defined
    4. An output type
    5. At least one named law hook (or explicit empty with justification)

    Returns (ok, errors).
    """
    errors = []
    if not motif.examples:
        errors.append(f"'{motif.name}': no positive examples")
    if not motif.anti_examples:
        errors.append(f"'{motif.name}': no anti-examples (counterexamples required)")
    if not motif.trigger_kinds and not motif.trigger_events:
        errors.append(f"'{motif.name}': no trigger conditions")
    if not motif.output_type:
        errors.append(f"'{motif.name}': no output type")
    # Law hook: either has required_laws or is in a known exception list
    # operational_requirement is explicitly law-free (it's a passthrough)
    _LAW_FREE_OK = {"operational_requirement"}
    if not motif.required_laws and motif.name not in _LAW_FREE_OK:
        errors.append(f"'{motif.name}': no required_laws (add laws or justify exception)")
    return len(errors) == 0, errors


def verify_all_motif_admissions() -> tuple[bool, list[str]]:
    """Verify all motifs meet admission bar."""
    all_errors = []
    for motif in ALL_MOTIFS:
        ok, errors = verify_motif_admission(motif)
        all_errors.extend(errors)
    return len(all_errors) == 0, all_errors


# Corpus promotion tiers
CORPUS_TIERS = {
    "experimental": "Not yet validated. May change or be removed.",
    "sentinel": "Tracked but non-blocking. Shows perception gaps.",
    "hard_gate": "Blocking. Must pass for release.",
    "atlas_seed": "Candidate for inclusion in the Smell Atlas.",
}


def coverage_report() -> dict[str, Any]:
    """Generate a coverage dashboard for the atlas. Pure.

    Tracks:
    - motifs with no anti-examples
    - laws with only one motif user
    - contrast pairs vs motif count
    - surfaces missing fixtures
    """
    # Motifs missing anti-examples
    missing_anti = [m.name for m in ALL_MOTIFS if not m.anti_examples]

    # Laws referenced by only one motif
    law_users: dict[str, list[str]] = {}
    for m in ALL_MOTIFS:
        for law_name in m.required_laws:
            law_users.setdefault(law_name, []).append(m.name)
    single_user_laws = {
        law: users[0] for law, users in law_users.items() if len(users) == 1
    }

    # Orphan laws (defined but not referenced by any motif)
    all_referenced = set()
    for m in ALL_MOTIFS:
        all_referenced.update(m.required_laws)
    orphan_laws = [l.name for l in ALL_LAWS if l.name not in all_referenced]

    # Contrast pair coverage
    motifs_with_contrast = set()
    for pair in CONTRAST_PAIRS:
        motifs_with_contrast.add(pair.motif_a)
        motifs_with_contrast.add(pair.motif_b)
    motifs_without_contrast = [m.name for m in ALL_MOTIFS if m.name not in motifs_with_contrast]

    return {
        "total_motifs": len(ALL_MOTIFS),
        "total_laws": len(ALL_LAWS),
        "total_contrast_pairs": len(CONTRAST_PAIRS),
        "motifs_missing_anti_examples": missing_anti,
        "single_user_laws": single_user_laws,
        "orphan_laws": orphan_laws,
        "motifs_without_contrast_pairs": motifs_without_contrast,
    }


def verify_pipeline_shape() -> tuple[bool, list[str]]:
    """Verify the pipeline infrastructure is intact. Pure.

    Checks that all required components exist and are wired correctly.
    This is a structural test, not a behavioral test.
    """
    errors = []

    # Laws must exist and have unique names
    law_names = [law.name for law in ALL_LAWS]
    if len(law_names) != len(set(law_names)):
        errors.append("duplicate law names")
    if not law_names:
        errors.append("no laws defined")

    # Motifs must reference valid laws
    for motif in ALL_MOTIFS:
        for law_name in motif.required_laws:
            if law_name not in LAW_INDEX:
                errors.append(f"motif '{motif.name}' references unknown law '{law_name}'")

    # Motifs must have unique names
    motif_names = [m.name for m in ALL_MOTIFS]
    if len(motif_names) != len(set(motif_names)):
        errors.append("duplicate motif names")

    # Contrast pairs must reference valid motifs
    for pair in CONTRAST_PAIRS:
        if pair.motif_a not in MOTIF_INDEX:
            errors.append(f"contrast pair '{pair.name}' references unknown motif '{pair.motif_a}'")
        if pair.motif_b not in MOTIF_INDEX:
            errors.append(f"contrast pair '{pair.name}' references unknown motif '{pair.motif_b}'")

    # Pipeline layers must be the canonical 9
    if len(PIPELINE_LAYERS) != 9:
        errors.append(f"expected 9 pipeline layers, got {len(PIPELINE_LAYERS)}")

    return len(errors) == 0, errors
