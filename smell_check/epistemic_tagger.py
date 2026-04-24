"""Epistemic tagger — deterministic clause-level smell parser.

Classifies each turn's semantic content using a 5-stage pure pipeline:
  1. normalize — clean whitespace, normalize quotes/dashes
  2. split_clauses — break into clause-level units at semantic boundaries
  3. extract_cues — find cue phrases from finite cue families
  4. score_surface_acts — weight cues into surface act scores
  5. emit_perception — produce ClausePerception objects

Surface acts (v2):
  commitment_formed — decision, agreement, schedule lock
  commitment_revised — changing a prior commitment
  conflict_detected — contradiction, disagreement, problem
  conflict_resolved — settlement, resolution
  uncertainty_expressed — doubt, hedge, unknown, TBD
  witness_reported — reported speech, attribution, evidence
  action_required — obligation, need, must-do
  pending_status — waiting, no response, unconfirmed

Backward-compatible with v1 event types for existing callers.

Pure. No I/O. No model calls. No network.
Same inputs → same outputs, always.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

# StoreProtocol, LedgerAction, LedgerEvent imported lazily inside
# emit_epistemic_events/tag_and_emit/backfill_epistemic_events
# so that classify_turn() can be imported without pydantic.

logger = logging.getLogger("surface.epistemic_tagger")


# ---------------------------------------------------------------------------
# V1 compatibility event type names
# ---------------------------------------------------------------------------

BELIEF_FORMED = "belief_formed"
BELIEF_REVISED = "belief_revised"
TENSION_DETECTED = "tension_detected"
TENSION_RESOLVED = "tension_resolved"
QUESTION_POSED = "question_posed"
EVIDENCE_CITED = "evidence_cited"

ALL_TYPES = frozenset({
    BELIEF_FORMED, BELIEF_REVISED, TENSION_DETECTED,
    TENSION_RESOLVED, QUESTION_POSED, EVIDENCE_CITED,
})


# ---------------------------------------------------------------------------
# V2 surface acts
# ---------------------------------------------------------------------------

COMMITMENT_FORMED = "commitment_formed"
COMMITMENT_REVISED = "commitment_revised"
COMMITMENT_HEDGED = "commitment_hedged"
CONFLICT_DETECTED = "conflict_detected"
CONFLICT_RESOLVED = "conflict_resolved"
UNCERTAINTY_EXPRESSED = "uncertainty_expressed"
WITNESS_REPORTED = "witness_reported"
ACTION_REQUIRED = "action_required"
PENDING_STATUS = "pending_status"

# V2 → V1 compatibility mapping
_V2_TO_V1 = {
    COMMITMENT_FORMED: BELIEF_FORMED,
    COMMITMENT_REVISED: BELIEF_REVISED,
    COMMITMENT_HEDGED: "commitment_hedged",  # new v1 event — no legacy equivalent
    CONFLICT_DETECTED: TENSION_DETECTED,
    CONFLICT_RESOLVED: TENSION_RESOLVED,
    UNCERTAINTY_EXPRESSED: QUESTION_POSED,
    WITNESS_REPORTED: EVIDENCE_CITED,
    ACTION_REQUIRED: TENSION_DETECTED,    # maps to CONSTRAINT in mother_types
    PENDING_STATUS: QUESTION_POSED,       # maps to UNCERTAINTY in mother_types
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Cue:
    """A cue phrase found in a clause."""
    kind: str       # cue family: "decision", "uncertainty", "witness", etc.
    value: str      # the matched text
    weight: int     # base score contribution


@dataclass
class SurfaceActScore:
    """A scored surface act for a clause."""
    act: str        # e.g. COMMITMENT_FORMED
    score: float    # 0-1 normalized
    raw_score: int  # pre-normalization


@dataclass
class ClausePerception:
    """Perception output for a single clause."""
    clause_id: str
    text: str
    span: tuple[int, int]       # (start, end) in original text
    cues: list[Cue]
    surface_acts: list[SurfaceActScore]
    actors: list[str]
    time_refs: list[str]
    reported: bool
    negated: bool
    abstained: bool


@dataclass
class EpistemicTag:
    """A single epistemic event detected in a turn. V1-compatible."""
    event_type: str
    confidence: float
    span: str = ""
    detail: str = ""
    # V2 enrichment
    surface_act: str = ""
    clause_id: str = ""


@dataclass
class TurnClassification:
    """Full epistemic classification of a single turn."""
    turn_id: str
    actor: str
    tags: list[EpistemicTag] = field(default_factory=list)
    claim_count: int = 0
    question_count: int = 0
    text_length: int = 0
    clauses: list[ClausePerception] = field(default_factory=list)
    perception_mode: str = "heuristic"  # "heuristic" | "model-assisted" (future)

    @property
    def event_types(self) -> list[str]:
        return [t.event_type for t in self.tags]

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "actor": self.actor,
            "event_types": self.event_types,
            "tags": [
                {
                    "event_type": t.event_type,
                    "confidence": round(t.confidence, 2),
                    "span": t.span[:200],
                    "detail": t.detail,
                    "surface_act": t.surface_act,
                }
                for t in self.tags
            ],
            "claim_count": self.claim_count,
            "question_count": self.question_count,
            "text_length": self.text_length,
        }


# ---------------------------------------------------------------------------
# Stage 1: Normalize
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    """Normalize whitespace, quotes, dashes. Pure."""
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2014", " -- ").replace("\u2013", " - ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Stage 2: Clause splitting
# ---------------------------------------------------------------------------

# Boundaries that split clauses
_CLAUSE_BOUNDARY = re.compile(
    r"(?<=[.!?;])\s+"                          # sentence-ending punctuation
    r"|(?:\s+(?:but|however|yet|except|although|though|while)\s+)"  # contrast conjunctions
    r"|(?:\s+(?:and|so|then)\s+(?=[A-Z]))"     # coordinating conjunctions before new sentence
, re.I)

# Reporting boundaries — split before these
_REPORTING_BOUNDARY = re.compile(
    r"(?=\b(?:\w+ (?:said|told me|promised|confirmed|mentioned|emailed|texted))\b)"
, re.I)


def _split_clauses(text: str) -> list[tuple[str, int, int]]:
    """Split text into clause-level units. Returns (text, start, end) tuples. Pure."""
    if not text.strip():
        return []

    # First pass: split on clause boundaries
    parts = _CLAUSE_BOUNDARY.split(text)
    clauses = []
    offset = 0

    for part in parts:
        part = part.strip()
        if not part:
            # Find where this empty part is in the original text
            offset = text.find(part, offset) + len(part) if part else offset
            continue
        start = text.find(part, offset)
        if start == -1:
            start = offset
        end = start + len(part)
        clauses.append((part, start, end))
        offset = end

    # Second pass: split reporting boundaries within clauses
    expanded = []
    for clause_text, start, end in clauses:
        sub_parts = _REPORTING_BOUNDARY.split(clause_text)
        sub_offset = start
        for sp in sub_parts:
            sp = sp.strip()
            if sp:
                sp_start = text.find(sp, sub_offset)
                if sp_start == -1:
                    sp_start = sub_offset
                expanded.append((sp, sp_start, sp_start + len(sp)))
                sub_offset = sp_start + len(sp)

    return expanded if expanded else clauses


# ---------------------------------------------------------------------------
# Stage 3: Cue extraction
# ---------------------------------------------------------------------------

# Cue families — finite, deterministic, human-language-first

_DECISION_CUES = [
    ("we decided", 5), ("we agreed", 5), ("we're going with", 5),
    ("the plan is", 4), ("locked in", 5), ("set for", 4),
    ("booked", 4), ("scheduled", 4), ("confirmed", 4),
    ("we'll", 3), ("i'll", 3), ("he'll", 3), ("she'll", 3), ("they'll", 3),
    ("going to", 3), ("will", 2), ("decided on", 5), ("decided to", 5),
    ("the decision is", 5), ("final answer", 5),
    ("guarantees", 4), ("guarantee", 4), ("ensures", 4),
    ("always", 3), ("never", 3), ("requires", 3),
]

_UNCERTAINTY_CUES = [
    ("not sure", 5), ("don't know", 5), ("might not", 4), ("may not", 4),
    ("probably not", 4), ("hard to say", 4), ("depends on", 4),
    ("still figuring out", 5), ("tbd", 5), ("to be determined", 5),
    ("haven't decided", 5), ("not certain", 5), ("not clear", 4),
    ("uncertain", 5), ("unclear", 5), ("open question", 5),
    ("untested", 5), ("unverified", 5), ("unproven", 5), ("unconfirmed", 4),
    ("not convinced", 4), ("maybe", 3), ("perhaps", 3), ("possibly", 3),
    ("might", 3), ("could be", 3), ("not necessarily", 4),
    ("i wonder", 3), ("who knows", 4), ("hard to tell", 4),
]

_WITNESS_CUES = [
    ("said", 3), ("told me", 4), ("told us", 4), ("promised", 5),
    ("confirmed", 4), ("mentioned", 3), ("emailed", 3), ("texted", 3),
    ("i heard", 3), ("per the email", 4), ("per the text", 4),
    ("from the thread", 3), ("last time", 2), ("according to", 4),
    ("source:", 3), ("they say", 3), ("he says", 3), ("she says", 3),
]

_CONFLICT_CUES = [
    ("that's not", 4), ("that doesn't work", 5), ("that won't work", 5),
    ("the problem is", 4), ("the issue is", 4), ("issue is", 4),
    ("i thought we said", 5), ("said something different", 5),
    ("that's different from", 4), ("doesn't match", 4), ("disagree", 5),
    ("contradicts", 5), ("conflicts with", 5), ("at odds", 4),
    ("wait,", 3), ("hold on", 3), ("no,", 3),
]

_RESOLUTION_CUES = [
    ("ok so we're going with", 5), ("then we're doing", 5),
    ("settled on", 5), ("resolved", 4), ("so the plan is", 5),
    ("we'll do", 4), ("let's go with", 5), ("let's just", 4),
    ("so we agreed", 5), ("final decision", 5), ("that settles it", 5),
    ("works for me", 3), ("sounds good", 3), ("fine with me", 3),
]

_ACTION_CUES = [
    ("need to", 5), ("needs to", 5), ("should", 3), ("must", 5),
    ("have to", 5), ("has to", 5), ("make sure", 4),
    ("don't forget", 5), ("someone has to", 5), ("who's going to", 4),
    ("please confirm", 5), ("follow up", 4), ("remind me", 4),
    ("can you", 3), ("could you", 3), ("would you", 3),
    ("let's make sure", 5), ("we need", 4), ("someone needs", 5),
    ("gotta", 3), ("better", 2),
]

_PENDING_CUES = [
    ("haven't heard back", 5), ("have not heard back", 5),
    ("still waiting", 5), ("no response", 5),
    ("pending", 4), ("to be confirmed", 5), ("following up", 4),
    ("not confirmed yet", 5), ("waiting on", 5),
    ("haven't confirmed", 5), ("have not confirmed", 5),
    ("need confirmation", 5),
    ("haven't responded", 5), ("have not responded", 5),
    ("no word", 4), ("tbc", 4), ("still no", 4),
    ("haven't gotten", 4), ("have not gotten", 4),
    ("waiting to hear", 5), ("radio silence", 4),
    ("not heard back", 5), ("heard nothing", 4),
]

_REVISION_CUES = [
    ("actually", 3), ("on second thought", 5), ("i was wrong", 5),
    ("wait, i thought", 4), ("scratch that", 5), ("never mind", 5),
    ("change of plans", 5), ("revised", 4), ("updated", 3),
    ("correction", 5), ("i take that back", 5), ("let me rephrase", 4),
    ("not what i meant", 5), ("i misspoke", 5),
]

_HEDGE_CUES = [
    # Softened agreement — sounds like yes but isn't fully committed
    ("yeah, probably", 5), ("i guess", 5), ("i suppose", 5),
    ("sure, when you get a chance", 5),
    ("probably", 4), ("seems fine", 4), ("seems okay", 4),
    ("should be okay", 4), ("should be fine", 4),
    ("likely", 3), ("not opposed", 4), ("fine, but", 4),
    ("i think so", 3), ("might work", 4), ("could work", 3),
    # Weak commitment language
    ("we can deal with that", 4), ("when it comes up", 4),
    ("if it becomes a problem", 4), ("for now", 3),
    ("let's see", 3), ("we'll figure it out", 4),
]

# Map cue families to surface acts
_CUE_FAMILY_TO_ACT = {
    "decision": COMMITMENT_FORMED,
    "uncertainty": UNCERTAINTY_EXPRESSED,
    "witness": WITNESS_REPORTED,
    "conflict": CONFLICT_DETECTED,
    "resolution": CONFLICT_RESOLVED,
    "action": ACTION_REQUIRED,
    "pending": PENDING_STATUS,
    "revision": COMMITMENT_REVISED,
    "hedge": COMMITMENT_HEDGED,
}

_ALL_CUE_FAMILIES = {
    "decision": _DECISION_CUES,
    "uncertainty": _UNCERTAINTY_CUES,
    "witness": _WITNESS_CUES,
    "conflict": _CONFLICT_CUES,
    "resolution": _RESOLUTION_CUES,
    "action": _ACTION_CUES,
    "pending": _PENDING_CUES,
    "hedge": _HEDGE_CUES,
    "revision": _REVISION_CUES,
}


def _extract_cues(clause_text: str) -> list[Cue]:
    """Find all matching cues in a clause. Pure."""
    lower = clause_text.lower()
    cues = []

    for family, cue_list in _ALL_CUE_FAMILIES.items():
        for phrase, weight in cue_list:
            if phrase in lower:
                cues.append(Cue(kind=family, value=phrase, weight=weight))

    return cues


# ---------------------------------------------------------------------------
# Stage 3b: Structural signals
# ---------------------------------------------------------------------------

_REPORTING_VERBS = frozenset({
    "said", "told", "promised", "confirmed", "mentioned",
    "emailed", "texted", "wrote", "replied", "responded",
})

_MODAL_VERBS = frozenset({
    "need", "must", "should", "have to", "has to",
    "will", "would", "could", "might", "may",
})

_NAMED_ACTOR_PATTERN = re.compile(r"\b[A-Z][a-z]+\b")
_TIME_PATTERN = re.compile(
    r"\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)?"
    r"|(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|(?:today|tomorrow|tonight|yesterday)"
    r"|(?:next|this|last)\s+(?:week|month|day)"
    r"|\d{1,2}/\d{1,2})\b", re.I
)


def _detect_structural_signals(clause_text: str) -> dict[str, Any]:
    """Detect structural signals in a clause. Pure."""
    lower = clause_text.lower()
    words = lower.split()

    return {
        "negated": any(w in words for w in ("not", "no", "never", "don't", "doesn't", "didn't", "haven't", "hasn't", "won't", "can't", "couldn't", "wouldn't")),
        "modal_present": any(m in lower for m in _MODAL_VERBS),
        "reporting_verb": any(v in words for v in _REPORTING_VERBS),
        "named_actors": _NAMED_ACTOR_PATTERN.findall(clause_text),
        "time_refs": _TIME_PATTERN.findall(clause_text),
        "first_person_plural": any(w in words for w in ("we", "we're", "we'll", "we've", "our", "us", "let's")),
        "question_mark": "?" in clause_text,
    }


# ---------------------------------------------------------------------------
# Stage 4: Score surface acts
# ---------------------------------------------------------------------------

_EMIT_THRESHOLD = 3  # minimum raw score to emit an act
_MAX_RAW_SCORE = 10  # for normalization


def _score_surface_acts(cues: list[Cue], signals: dict[str, Any]) -> list[SurfaceActScore]:
    """Score each surface act based on cues and structural signals. Pure."""
    scores: dict[str, int] = {}

    # Accumulate cue weights by surface act
    for cue in cues:
        act = _CUE_FAMILY_TO_ACT.get(cue.kind)
        if act:
            scores[act] = scores.get(act, 0) + cue.weight

    # Structural boosting
    if signals["reporting_verb"] and signals["named_actors"]:
        scores[WITNESS_REPORTED] = scores.get(WITNESS_REPORTED, 0) + 2

    if signals["first_person_plural"]:
        if COMMITMENT_FORMED in scores:
            scores[COMMITMENT_FORMED] += 2

    if signals["question_mark"]:
        scores[UNCERTAINTY_EXPRESSED] = scores.get(UNCERTAINTY_EXPRESSED, 0) + 3

    if signals["modal_present"] and signals["negated"]:
        scores[UNCERTAINTY_EXPRESSED] = scores.get(UNCERTAINTY_EXPRESSED, 0) + 2

    if signals["time_refs"]:
        if COMMITMENT_FORMED in scores:
            scores[COMMITMENT_FORMED] += 1
        if ACTION_REQUIRED in scores:
            scores[ACTION_REQUIRED] += 1

    # Filter by threshold, normalize, sort
    results = []
    for act, raw in scores.items():
        if raw >= _EMIT_THRESHOLD:
            normalized = min(1.0, raw / _MAX_RAW_SCORE)
            results.append(SurfaceActScore(act=act, score=round(normalized, 2), raw_score=raw))

    results.sort(key=lambda s: s.score, reverse=True)
    return results


# ---------------------------------------------------------------------------
# Stage 5: Emit perception
# ---------------------------------------------------------------------------

def _emit_perception(
    clause_text: str,
    clause_id: str,
    span: tuple[int, int],
    cues: list[Cue],
    acts: list[SurfaceActScore],
    signals: dict[str, Any],
) -> ClausePerception:
    """Produce a ClausePerception from scored acts. Pure."""
    return ClausePerception(
        clause_id=clause_id,
        text=clause_text,
        span=span,
        cues=cues,
        surface_acts=acts,
        actors=signals.get("named_actors", []),
        time_refs=signals.get("time_refs", []),
        reported=signals.get("reporting_verb", False),
        negated=signals.get("negated", False),
        abstained=len(acts) == 0,
    )


# ---------------------------------------------------------------------------
# Main entry point: classify_turn (v2, backward-compatible)
# ---------------------------------------------------------------------------

def classify_turn(
    text: str,
    turn_id: str = "",
    actor: str = "",
    *,
    prior_turns: list[str] | None = None,
) -> TurnClassification:
    """Classify a turn's semantic content using clause-level smell parsing.

    5-stage pure pipeline:
      normalize → split_clauses → extract_cues → score_acts → emit_perception

    Returns a TurnClassification with:
      - v1-compatible EpistemicTags (for existing callers)
      - v2 ClausePerception objects (for new callers)

    Args:
        text: The turn's text content.
        turn_id: ID of the turn artifact.
        actor: The actor who produced this turn.
        prior_turns: Optional prior turns for context (reserved for future use).
    """
    result = TurnClassification(
        turn_id=turn_id,
        actor=actor,
        text_length=len(text),
    )

    if not text.strip():
        return result

    # Stage 1: Normalize
    normalized = _normalize_text(text)

    # Stage 2: Split into clauses
    clause_tuples = _split_clauses(normalized)

    # If splitting produced nothing, treat entire text as one clause
    if not clause_tuples:
        clause_tuples = [(normalized, 0, len(normalized))]

    # Stages 3-5: Process each clause
    clauses = []
    for i, (clause_text, start, end) in enumerate(clause_tuples):
        clause_text = clause_text.strip()
        if not clause_text or len(clause_text) < 3:
            continue

        clause_id = f"c{i}"

        # Stage 3: Extract cues
        cues = _extract_cues(clause_text)

        # Stage 3b: Structural signals
        signals = _detect_structural_signals(clause_text)

        # Stage 4: Score surface acts
        acts = _score_surface_acts(cues, signals)

        # Stage 5: Emit perception
        perception = _emit_perception(clause_text, clause_id, (start, end), cues, acts, signals)
        clauses.append(perception)

    result.clauses = clauses

    # Convert to v1-compatible EpistemicTags
    for clause in clauses:
        for act_score in clause.surface_acts:
            v1_type = _V2_TO_V1.get(act_score.act, BELIEF_FORMED)
            result.tags.append(EpistemicTag(
                event_type=v1_type,
                confidence=act_score.score,
                span=clause.text[:200],
                detail=f"{act_score.act} (score={act_score.raw_score})",
                surface_act=act_score.act,
                clause_id=clause.clause_id,
            ))

    # Count questions and claims for backward compat
    result.question_count = sum(1 for c in clauses if any(
        a.act in (UNCERTAINTY_EXPRESSED, PENDING_STATUS) for a in c.surface_acts
    ))
    result.claim_count = sum(1 for c in clauses if any(
        a.act in (COMMITMENT_FORMED, WITNESS_REPORTED, ACTION_REQUIRED) for a in c.surface_acts
    ))

    # Sort tags by confidence
    result.tags.sort(key=lambda t: t.confidence, reverse=True)

    return result


# ---------------------------------------------------------------------------
# V1 orchestration functions (lazy imports, app-layer, not trunk)
# ---------------------------------------------------------------------------

def emit_epistemic_events(
    store: "StoreProtocol",
    turn_id: str,
    classification: TurnClassification,
    *,
    topic_handle: str | None = None,
    model_name: str | None = None,
    provider: str | None = None,
) -> list:
    """Emit ledger events for each epistemic tag in a classification.

    Returns the list of emitted events.
    """
    from .kernel import StoreProtocol  # noqa: F811
    from .models import LedgerAction, LedgerEvent

    if not classification.tags:
        return []

    events: list[LedgerEvent] = []

    for tag in classification.tags:
        if tag.confidence < 0.4:
            continue

        event = LedgerEvent(
            action=LedgerAction.epistemic_event,
            subject_id=turn_id,
            content={
                "event_type": tag.event_type,
                "confidence": round(tag.confidence, 4),
                "span": tag.span[:500],
                "detail": tag.detail,
                "topic_handle": topic_handle,
                "model_name": model_name,
                "provider": provider,
            },
        )
        events.append(event)

        store.append_ledger_event(event)

    return events


def tag_and_emit(
    store: "StoreProtocol",
    text: str,
    turn_id: str,
    actor: str,
    *,
    topic_handle: str | None = None,
    model_name: str | None = None,
    provider: str | None = None,
) -> TurnClassification:
    """Convenience: classify a turn and emit events in one call.

    This is the primary entry point for the relay hook.
    """
    classification = classify_turn(text, turn_id=turn_id, actor=actor)
    emit_epistemic_events(
        store,
        turn_id,
        classification,
        topic_handle=topic_handle,
        model_name=model_name,
        provider=provider,
    )
    return classification


# ---------------------------------------------------------------------------
# Backfill — batch-tag existing thread history
# ---------------------------------------------------------------------------

def backfill_epistemic_events(
    store: "StoreProtocol",
    *,
    skip_human: bool = True,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Backfill epistemic events for all existing conversation turns.

    Iterates every conversation_turn artifact, classifies it, and emits
    epistemic events for any that don't already have them. Idempotent —
    skips turns that already have epistemic events.

    Args:
        store: Storage backend.
        skip_human: If True (default), skip human-authored turns.
        dry_run: If True, classify but don't emit events.

    Returns:
        Summary dict with counts and per-turn results.
    """
    from .models import ArtifactType

    # Collect turn IDs that already have epistemic events (idempotency)
    # Use read_all to avoid query_ledger's default limit
    if force:
        already_tagged: set[str] = set()  # force re-tags everything
    else:
        all_events = store.read_all_ledger_events()
        already_tagged = {
            e.subject_id for e in all_events
            if e.action.value == "epistemic.event"
        }

    # Iterate all conversation turns
    art_ids = store.list_artifact_ids_by_type("conversation_turn")
    total = 0
    tagged = 0
    skipped_human = 0
    skipped_existing = 0
    skipped_empty = 0
    events_emitted = 0
    per_type: dict[str, int] = {}
    per_actor: dict[str, int] = {}

    for aid in art_ids:
        art = store.get_artifact(aid)
        if art is None or art.type != ArtifactType.conversation_turn:
            continue

        total += 1
        turn_id = art.content.get("turn_id", "")
        actor = art.content.get("actor", "")
        text = art.content.get("text", "")

        # Skip human turns
        if skip_human and "human" in actor.lower():
            skipped_human += 1
            continue

        # Skip already tagged
        if turn_id in already_tagged:
            skipped_existing += 1
            continue

        # Skip empty
        if not text.strip():
            skipped_empty += 1
            continue

        # Classify
        classification = classify_turn(text, turn_id=turn_id, actor=actor)

        if not dry_run:
            emitted = emit_epistemic_events(store, turn_id, classification)
            events_emitted += len(emitted)
            for evt in emitted:
                et = evt.content.get("event_type", "?")
                per_type[et] = per_type.get(et, 0) + 1
                per_actor[actor] = per_actor.get(actor, 0) + 1

        tagged += 1

    return {
        "total_turns": total,
        "tagged": tagged,
        "skipped_human": skipped_human,
        "skipped_existing": skipped_existing,
        "skipped_empty": skipped_empty,
        "events_emitted": events_emitted,
        "per_type": per_type,
        "per_actor": per_actor,
        "dry_run": dry_run,
    }
