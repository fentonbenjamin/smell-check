"""Mother type system — the canonical bridge from source to typed substrate units.

Pure functions. No I/O. No model calls.

The five mother types (validated across 13 independent corpora):
  CONTRACT    — what this thing promises (if A then B)
  CONSTRAINT  — what cannot be true simultaneously
  UNCERTAINTY — where the system declares its limits
  RELATION    — how claims connect (supports/attacks/precedes)
  WITNESS     — who observed this and when, under what authority

This module produces TypedUnit v0 — the canonical transport object that
everything in the substrate operates on. The typed unit carries its own
witness, its own mother type, its own binding tier. It's the medium that
closes the second-order loop and enables operational homoiconicity.

Spec: /Users/Shared/substrate/specs/type_system_v0.md
"""

from __future__ import annotations

import hashlib
import time
import secrets
from typing import Any


# ---------------------------------------------------------------------------
# Mother type constants
# ---------------------------------------------------------------------------

CONTRACT = "CONTRACT"
CONSTRAINT = "CONSTRAINT"
UNCERTAINTY = "UNCERTAINTY"
RELATION = "RELATION"
WITNESS = "WITNESS"

ALL_MOTHER_TYPES = frozenset({CONTRACT, CONSTRAINT, UNCERTAINTY, RELATION, WITNESS})

# ---------------------------------------------------------------------------
# Tagger event → mother type mapping
# ---------------------------------------------------------------------------

_EVENT_TO_MOTHER_TYPE = {
    "belief_formed": CONTRACT,
    "tension_detected": CONSTRAINT,
    "question_posed": UNCERTAINTY,
    "evidence_cited": WITNESS,
    "belief_revised": RELATION,
    "tension_resolved": RELATION,
}

# ---------------------------------------------------------------------------
# Surrogate claim_type → mother type mapping (for existing sieve claims)
# ---------------------------------------------------------------------------

_SURROGATE_TO_MOTHER = {
    "fact": CONTRACT,
    "principle": CONTRACT,
    "design_decision": CONTRACT,
    "guarantee": WITNESS,
    "observation": RELATION,
    "hypothesis": UNCERTAINTY,
    "constraint": CONSTRAINT,
    "question": UNCERTAINTY,
    "framework_fragment": RELATION,
    "claim": CONTRACT,  # reclassified from hypothesis without hedging
}

# ---------------------------------------------------------------------------
# Mother type → sieve claim_type (reverse mapping for sieve compatibility)
# ---------------------------------------------------------------------------

_MOTHER_TO_SIEVE_TYPE = {
    CONTRACT: "fact",
    CONSTRAINT: "constraint",
    UNCERTAINTY: "hypothesis",
    RELATION: "observation",
    WITNESS: "guarantee",
}


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def event_to_mother_type(event_type: str) -> str | None:
    """Map an epistemic tagger event type to a mother type. Pure."""
    return _EVENT_TO_MOTHER_TYPE.get(event_type)


def surrogate_to_mother_type(claim_type: str) -> str:
    """Map an existing sieve claim_type to a mother type. Pure.
    Defaults to CONTRACT for unknown types."""
    return _SURROGATE_TO_MOTHER.get(claim_type.lower(), CONTRACT)


def mother_to_sieve_type(mother_type: str) -> str:
    """Map a mother type back to sieve claim_type for compatibility. Pure."""
    return _MOTHER_TO_SIEVE_TYPE.get(mother_type, "fact")


def classify_claim_mother_type(claim: dict[str, Any]) -> str:
    """Determine the mother type of a claim from available signals. Pure.

    Priority:
    1. Explicit mother_type field (already classified)
    2. epistemic_event field (from tagger)
    3. claim_type field (surrogate mapping)
    """
    # Already classified
    if claim.get("mother_type") in ALL_MOTHER_TYPES:
        return claim["mother_type"]

    # From tagger event
    event = claim.get("epistemic_event")
    if event:
        mt = event_to_mother_type(event)
        if mt:
            return mt

    # From surrogate claim_type
    ct = claim.get("claim_type", "")
    if ct:
        return surrogate_to_mother_type(ct)

    return CONTRACT  # default


def enrich_claims_with_mother_types(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add mother_type field to each claim. Pure. Non-mutating — returns new list."""
    result = []
    for claim in claims:
        c = dict(claim)
        c["mother_type"] = classify_claim_mother_type(c)
        result.append(c)
    return result


# ---------------------------------------------------------------------------
# ID generation (inline, no dependency on ids.py for substrate portability)
# ---------------------------------------------------------------------------

def _generate_id(prefix: str) -> str:
    """Time-sortable unique ID. Pure enough (time + entropy)."""
    ts = format(int(time.time() * 1000), "x")
    rand = secrets.token_hex(3)
    return f"{prefix}_{ts}_{rand}"


# ---------------------------------------------------------------------------
# Dev-first subtype lattice
# ---------------------------------------------------------------------------

_DEV_SUBTYPES: dict[str, dict[str, str]] = {
    CONTRACT: {
        "behavioral_guarantee": "behavioral_guarantee",
        "interface_promise": "interface_promise",
        "determinism_guarantee": "determinism_guarantee",
        "compatibility_claim": "compatibility_claim",
        "execution_contract": "execution_contract",
    },
    CONSTRAINT: {
        "version_incompatibility": "version_incompatibility",
        "impurity_boundary": "impurity_boundary",
        "runtime_requirement": "runtime_requirement",
        "ordering_constraint": "ordering_constraint",
        "resource_constraint": "resource_constraint",
    },
    UNCERTAINTY: {
        "unverified_path": "unverified_path",
        "dynamic_behavior_gap": "dynamic_behavior_gap",
        "heuristic_inference": "heuristic_inference",
        "coverage_gap": "coverage_gap",
        "open_edge_case": "open_edge_case",
    },
    RELATION: {
        "calls": "calls",
        "wraps": "wraps",
        "depends_on": "depends_on",
        "derives_from": "derives_from",
        "conflicts_with": "conflicts_with",
        "narrows": "narrows",
        "stamps": "stamps",
    },
    WITNESS: {
        "ast_evidence": "ast_evidence",
        "test_evidence": "test_evidence",
        "lockfile_evidence": "lockfile_evidence",
        "ci_evidence": "ci_evidence",
        "runtime_trace_evidence": "runtime_trace_evidence",
        "human_review_evidence": "human_review_evidence",
    },
}

# Heuristic subtype inference from text signals
_SUBTYPE_SIGNALS: dict[str, list[tuple[str, list[str]]]] = {
    CONTRACT: [
        ("determinism_guarantee", ["deterministic", "same input", "pure function", "no side effect"]),
        ("interface_promise", ["returns", "accepts", "signature", "interface", "api"]),
        ("behavioral_guarantee", ["always", "must", "guarantees", "ensures", "will"]),
        ("compatibility_claim", ["compatible", "supports", "works with", "interop"]),
    ],
    CONSTRAINT: [
        ("impurity_boundary", ["impure", "i/o", "side effect", "not pure", "mutable"]),
        ("runtime_requirement", ["requires", "needs", "depends on", "must have"]),
        ("version_incompatibility", ["incompatible", "breaking", "deprecated", "removed"]),
        ("ordering_constraint", ["before", "after", "first", "then", "order"]),
    ],
    UNCERTAINTY: [
        ("open_edge_case", ["edge case", "corner case", "what if", "what about"]),
        ("unverified_path", ["untested", "unverified", "not proven", "unclear"]),
        ("heuristic_inference", ["heuristic", "approximate", "estimated", "likely"]),
        ("dynamic_behavior_gap", ["dynamic", "runtime", "unpredictable", "varies"]),
    ],
    RELATION: [
        ("depends_on", ["depends on", "requires", "needs", "imports"]),
        ("derives_from", ["derived from", "based on", "built from", "extends"]),
        ("conflicts_with", ["contradicts", "conflicts", "incompatible with", "opposes"]),
        ("calls", ["calls", "invokes", "executes", "runs"]),
    ],
    WITNESS: [
        ("ast_evidence", ["ast", "parse", "syntax tree", "source code"]),
        ("test_evidence", ["test", "passing", "assertion", "coverage"]),
        ("human_review_evidence", ["reviewed", "approved", "ratified", "confirmed"]),
        ("runtime_trace_evidence", ["runtime", "trace", "execution", "observed at"]),
    ],
}


def infer_subtype(mother_type: str, text: str) -> str:
    """Infer dev-first subtype from text signals. Pure.
    Returns subtype string or 'unknown_subtype'."""
    signals = _SUBTYPE_SIGNALS.get(mother_type, [])
    lower = text.lower()
    for subtype, keywords in signals:
        if any(kw in lower for kw in keywords):
            return subtype
    return "unknown_subtype"


# ---------------------------------------------------------------------------
# Witness stub
# ---------------------------------------------------------------------------

def make_witness(
    witness_type: str = "raw_turn",
    source_kind: str = "conversation",
    source_ref: str = "",
    observed_by: str = "",
    authority: str = "system",
) -> dict[str, Any]:
    """Construct a Witness v0 object. Pure (except ID generation)."""
    return {
        "id": _generate_id("wit"),
        "witness_type": witness_type,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "observed_by": observed_by,
        "authority": authority,
        "attested_by": None,
        "schema_version": "substrate.witness.v0",
    }


# ---------------------------------------------------------------------------
# TypedUnit v0 — the canonical transport object
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "substrate.typed_unit.v0"


def make_typed_unit(
    text: str,
    mother_type: str,
    *,
    subtype: str = "unknown_subtype",
    binding_tier: str = "observed",
    confidence: float | None = None,
    authority: str = "system",
    source_refs: list[str] | None = None,
    witness_refs: list[str] | None = None,
    relation_refs: list[str] | None = None,
    type_key: str = "",
    claim_role: str = "",
    claim_type: str = "",
    epistemic_event: str = "",
    actor: str = "",
    turn_id: str = "",
    timestamp: str = "",
    source: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a canonical TypedUnit v0. Pure (except ID generation).

    This is the transport object everything in the substrate operates on.
    Required: text, mother_type, binding_tier, schema_version.
    Everything else is strongly recommended but optional.
    """
    if mother_type not in ALL_MOTHER_TYPES:
        mother_type = CONTRACT  # safe default

    unit = {
        "id": _generate_id("tu"),
        "text": text,
        "mother_type": mother_type,
        "subtype": subtype,
        "binding_tier": binding_tier,
        "confidence": confidence,
        "authority": authority,
        "source_refs": source_refs or [],
        "witness_refs": witness_refs or [],
        "relation_refs": relation_refs or [],
        "type_key": type_key,
        "claim_role": claim_role,
        # Compatibility fields (sieve still uses these)
        "claim_type": claim_type or mother_to_sieve_type(mother_type),
        "epistemic_event": epistemic_event,
        "actor": actor,
        "turn_id": turn_id,
        "timestamp": timestamp,
        "source": source,
        "evidence_refs": source_refs or ([turn_id] if turn_id else []),
        "schema_version": SCHEMA_VERSION,
    }
    if extra:
        unit.update(extra)
    return unit


# ---------------------------------------------------------------------------
# Tagger → TypedUnit v0 (the real bridge)
# ---------------------------------------------------------------------------

def tagger_to_typed_units(
    text: str,
    tags: list[dict[str, Any]],
    *,
    actor: str = "",
    turn_id: str = "",
    timestamp: str = "",
    source: str = "",
) -> list[dict[str, Any]]:
    """Convert epistemic tagger output into canonical TypedUnit v0 objects. Pure.

    Each tag with confidence >= 0.4 becomes a typed unit with:
    - mother_type from the tag's event type
    - subtype inferred from text signals
    - witness stub attached
    - full provenance (actor, turn_id, timestamp, source)
    """
    units = []
    for tag in tags:
        conf = tag.get("confidence", 0)
        event_type = tag.get("event_type", "")

        # Type-aware confidence floor:
        # Questions, challenges, and revisions are near-sacred signals —
        # they need stronger evidence to suppress than to keep.
        # Other signals use the standard 0.4 threshold.
        _CHALLENGE_EVENTS = frozenset({
            "question_posed", "belief_revised", "contradiction_detected",
        })
        min_conf = 0.2 if event_type in _CHALLENGE_EVENTS else 0.4
        if conf < min_conf:
            continue

        mother_type = event_to_mother_type(event_type)
        if not mother_type:
            continue

        span = tag.get("span", "").strip()
        unit_text = span if span and len(span) > 15 else text[:500]

        # Infer subtype from text
        subtype = infer_subtype(mother_type, unit_text)

        # Create witness for this unit
        witness = make_witness(
            witness_type="raw_turn",
            source_kind="conversation",
            source_ref=turn_id,
            observed_by=f"epistemic_tagger.{event_type}",
            authority="system",
        )

        # Carry source anchor through for drillback
        extra = {}
        source_span = tag.get("source_span")
        clause_id = tag.get("clause_id", "")
        if source_span:
            extra["source_span"] = source_span
        if clause_id:
            extra["clause_id"] = clause_id

        unit = make_typed_unit(
            text=unit_text,
            mother_type=mother_type,
            subtype=subtype,
            confidence=round(conf, 2),
            authority="model" if "agent:" in actor or "claude" in actor.lower() else "human",
            source_refs=[turn_id] if turn_id else [],
            witness_refs=[witness["id"]],
            epistemic_event=event_type,
            actor=actor,
            turn_id=turn_id,
            timestamp=timestamp,
            source=source,
            extra=extra,
        )
        # Attach the witness object inline for transport
        unit["_witness"] = witness
        units.append(unit)

    # No fallback. If the tagger found nothing, we abstain.
    # The old fallback created a CONTRACT unit for any text >50 chars,
    # which caused raw code and diffs to appear as "decided" — a
    # dangerous false positive for a code review tool.
    # Abstention is honest. Fake certainty is not.
    if not units:
        return units

    # --- dead code below kept as comment for history ---
    # Previously: substantial text with no tags → default CONTRACT unit
    # if not units and len(text.strip()) > 50:
    #     unit = make_typed_unit(text=text[:500], mother_type=CONTRACT, ...)
    if False:  # noqa: preserve the old signature for git blame
        witness = make_witness(
            witness_type="raw_turn",
            source_kind="conversation",
            source_ref=turn_id,
            observed_by="mother_types.fallback",
            authority="system",
        )
        unit = make_typed_unit(
            text=text[:500],
            mother_type=CONTRACT,
            subtype="unknown_subtype",
            confidence=0.5,
            authority="human" if "human" in actor.lower() else "system",
            source_refs=[turn_id] if turn_id else [],
            witness_refs=[witness["id"]],
            actor=actor,
            turn_id=turn_id,
            timestamp=timestamp,
            source=source,
        )
        unit["_witness"] = witness
        units.append(unit)

    return units


# ---------------------------------------------------------------------------
# Compatibility adapter (typed units → old sieve claim format)
# ---------------------------------------------------------------------------

def typed_units_to_sieve_claims(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert TypedUnit v0 objects to sieve-compatible claim dicts. Pure.
    Preserves all typed fields while ensuring the sieve can consume them."""
    # TypedUnit v0 already includes claim_type and evidence_refs
    # for backward compat — just return as-is. The sieve will see
    # both mother_type (new) and claim_type (compat).
    return units


# ---------------------------------------------------------------------------
# Legacy compatibility — keep tagger_to_claims working
# ---------------------------------------------------------------------------

def tagger_to_claims(
    text: str,
    tags: list[dict[str, Any]],
    *,
    actor: str = "",
    turn_id: str = "",
    timestamp: str = "",
    source: str = "",
) -> list[dict[str, Any]]:
    """Legacy adapter. Calls tagger_to_typed_units internally.
    Returns typed units that are also sieve-compatible."""
    return tagger_to_typed_units(
        text, tags,
        actor=actor, turn_id=turn_id, timestamp=timestamp, source=source,
    )
