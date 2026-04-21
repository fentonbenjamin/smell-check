"""Projection lenses — governed state → human-readable smell check output.

The governed state is the canonical object. Projections are lenses over it.
Same engine, different leaves.

The output is NOT backend-shaped buckets.
The output reads like a sharp review — findings first, proof underneath.

Three layers:
  1. Human-readable interpretation — summary, findings, what to do
  2. Structural reasoning — typed basis for each finding (drillback)
  3. Source drillback — clause/span anchors to the original input
"""

from __future__ import annotations

from typing import Any


def project_smell_check(governed_state: dict[str, Any]) -> dict[str, Any]:
    """Project governed state into smell check findings.

    The output reads like an inline review:
      - what smells funny
      - why it smells funny
      - what seems stable
      - what needs attention
      - what to do next

    Not buckets. Findings.
    """
    promoted = governed_state.get("promoted", [])
    contested = governed_state.get("contested", [])
    deferred = governed_state.get("deferred", [])

    findings = []
    stable_points = []
    open_questions = []

    for claim in promoted:
        mother_type = claim.get("mother_type", "")
        text = claim.get("text", "")
        subtype = claim.get("subtype", "")
        confidence = claim.get("confidence")

        # Source anchor — where in the original input this came from
        # Code findings have _where with file/line/function
        # Thread findings have source_span with char offsets
        code_where = claim.get("_where")
        source_span = claim.get("source_span")
        clause_id = claim.get("clause_id", "")
        if code_where:
            where = code_where
            where["text"] = text
        else:
            where = {
                "text": text,
                "clause_id": clause_id,
            }
            if source_span:
                where["char_offset"] = source_span

        drillback = {
            "mother_type": mother_type,
            "subtype": subtype,
            "confidence": confidence,
            "claim_type": claim.get("claim_type", ""),
            "epistemic_event": claim.get("epistemic_event", ""),
        }

        finding_kind = claim.get("_finding_kind", "")
        evidence_basis = claim.get("_evidence_basis", "")
        signals = claim.get("_signals", [])

        # Code-specific rendering by finding kind (structural, not prose)
        if finding_kind == "impurity":
            signal_detail = f": {', '.join(signals)}" if signals else ""
            findings.append({
                "judgment": f"{_fn_name(where)} has side effects{signal_detail}",
                "because": f"AST analysis found I/O or impure operations in this function.",
                "where": where,
                "what_to_do": "Review whether the impurity is intentional.",
                "drillback": drillback,
            })
        elif finding_kind == "violation":
            findings.append({
                "judgment": f"{_fn_name(where)}: {_violation_text(text)}",
                "because": "Structural analysis found a constraint violation.",
                "where": where,
                "what_to_do": "Review the function's behavior vs its contract.",
                "drillback": drillback,
            })
        elif finding_kind == "provenance_gap":
            findings.append({
                "judgment": f"External dependency with no provenance: {text}",
                "because": "This import appears external to the repo.",
                "where": where,
                "what_to_do": "Verify the dependency source.",
                "drillback": drillback,
            })
        elif finding_kind == "exception_safety":
            findings.append({
                "judgment": f"{_fn_name(where)}: {_violation_text(text)}",
                "because": "Exception handling issue detected by AST analysis.",
                "where": where,
                "what_to_do": "Add proper error handling or make the exception handling explicit.",
                "drillback": drillback,
            })
        elif finding_kind == "guard_present":
            stable_points.append({
                "judgment": f"{_fn_name(where)} {text.split(' has ', 1)[1] if ' has ' in text else 'has validation'}",
                "because": "Input validation guards detected by AST.",
                "where": where,
                "drillback": drillback,
            })
        elif finding_kind == "global_mutation":
            findings.append({
                "judgment": f"{_fn_name(where)} mutates global state",
                "because": "Uses 'global' keyword — shared mutable state across calls.",
                "where": where,
                "what_to_do": "Consider passing state explicitly instead of using globals.",
                "drillback": drillback,
            })
        elif finding_kind == "purity":
            stable_points.append({
                "judgment": f"{_fn_name(where)} is structurally pure",
                "because": "No I/O, no side effects, no global state detected by AST.",
                "where": where,
                "drillback": drillback,
            })
        elif finding_kind in ("significant_removal", "large_addition", "file_change"):
            findings.append({
                "judgment": text,
                "because": f"Diff structural signal: {finding_kind.replace('_', ' ')}.",
                "where": where,
                "what_to_do": "Review the change.",
                "drillback": drillback,
            })
        # Thread-specific rendering by mother type (prose, not structural)
        elif mother_type == "UNCERTAINTY":
            open_questions.append({
                "judgment": f"This is still unclear: {text}",
                "because": "Expressed as uncertainty.",
                "where": where,
                "what_to_do": "Confirm or resolve before relying on it.",
                "drillback": drillback,
            })
        elif mother_type == "CONSTRAINT" and _looks_actionable(text):
            findings.append({
                "judgment": f"Someone needs to act: {text}",
                "because": "This is an obligation or required action.",
                "where": where,
                "what_to_do": "Assign it or confirm it's handled.",
                "drillback": drillback,
            })
        elif mother_type == "CONSTRAINT":
            findings.append({
                "judgment": f"Requirement: {text}",
                "because": "Expressed as a constraint.",
                "where": where,
                "what_to_do": "Verify it's being respected.",
                "drillback": drillback,
            })
        elif mother_type == "WITNESS":
            stable_points.append({
                "judgment": f"Reported: {text}",
                "because": "Attributed to a source or observation.",
                "where": where,
                "drillback": drillback,
            })
        elif mother_type == "CONTRACT":
            # Check if the tagger already classified this as a decision
            surface_act = claim.get("epistemic_event", "")
            is_decision = any(cue in text.lower() for cue in (
                "we decided", "we agreed", "decided to", "the plan is",
                "confirmed", "locked in", "set for",
            ))
            if is_decision:
                stable_points.append({
                    "judgment": f"Decided: {text}",
                    "because": "Expressed as a decision or agreement.",
                    "where": where,
                    "drillback": drillback,
                })
            elif _looks_actionable(text):
                findings.append({
                    "judgment": f"Action item: {text}",
                    "because": "This reads as a commitment to do something.",
                    "where": where,
                    "what_to_do": "Track it.",
                    "drillback": drillback,
                })
            else:
                stable_points.append({
                    "judgment": f"Decided: {text}",
                    "because": "Expressed as a decision or agreement.",
                    "where": where,
                    "drillback": drillback,
                })
        elif mother_type == "RELATION":
            stable_points.append({
                "judgment": f"Noted: {text}",
                "because": "Relates to other points.",
                "where": where,
                "drillback": drillback,
            })
        else:
            stable_points.append({
                "judgment": text,
                "because": "Promoted without a specific finding kind.",
                "where": where,
                "drillback": drillback,
            })

    for claim in contested:
        text = claim.get("text", "")
        findings.append({
            "judgment": f"This smells funny: {text}",
            "because": "Conflicting signals — this claim is contested by other evidence in the thread.",
            "where": {"text": text, "clause_id": claim.get("clause_id", "")},
            "what_to_do": "Resolve the conflict before relying on it.",
            "drillback": {"status": "contested"},
        })

    for claim in deferred:
        text = claim.get("text", claim.get("claim_text", ""))
        reason = claim.get("_defer_reason", "insufficient evidence")
        open_questions.append({
            "judgment": f"Not enough to go on: {text}",
            "because": f"Deferred — {reason}.",
            "where": {"text": text, "clause_id": claim.get("clause_id", "")},
            "what_to_do": "Gather more evidence or clarify.",
            "drillback": {"status": "deferred", "reason": reason},
        })

    # Build summary
    total_findings = len(findings) + len(open_questions)
    if total_findings == 0 and stable_points:
        summary = "Everything looks stable. No smells detected."
    elif total_findings == 0:
        summary = "Not enough signal to judge. Try a longer thread."
    elif len(findings) > len(stable_points):
        summary = f"{len(findings)} thing{'s' if len(findings) != 1 else ''} smell{'s' if len(findings) == 1 else ''} off. {len(open_questions)} open question{'s' if len(open_questions) != 1 else ''}."
    else:
        summary = f"Mostly stable, but {len(findings)} finding{'s' if len(findings) != 1 else ''} and {len(open_questions)} open question{'s' if len(open_questions) != 1 else ''} to check."

    return {
        "summary": summary,
        "findings": findings,
        "stable_points": stable_points,
        "open_questions": open_questions,
    }


# ---------------------------------------------------------------------------
# Legacy projections (for consumer page / backward compat)
# ---------------------------------------------------------------------------

def project_consumer(governed_state: dict[str, Any]) -> dict[str, Any]:
    """Project governed state into consumer-friendly cards."""
    promoted = governed_state.get("promoted", [])
    contested = governed_state.get("contested", [])
    deferred = governed_state.get("deferred", [])

    decided = []
    to_do = []
    needs_confirmation = []
    unclear = []
    waiting_on = []

    for claim in promoted:
        mother_type = claim.get("mother_type", "")
        text = claim.get("text", "")
        card = {"text": text, "source_type": mother_type}

        if mother_type == "UNCERTAINTY":
            unclear.append(card)
        elif mother_type == "CONSTRAINT" and _looks_actionable(text):
            to_do.append(card)
        elif mother_type == "CONSTRAINT":
            needs_confirmation.append(card)
        elif _looks_actionable(text):
            to_do.append(card)
        else:
            decided.append(card)

    for claim in contested:
        needs_confirmation.append({"text": claim.get("text", ""), "source_type": "contested"})

    for claim in deferred:
        waiting_on.append({
            "text": claim.get("text", claim.get("claim_text", "")),
            "source_type": "deferred",
            "reason": claim.get("_defer_reason", ""),
        })

    return {
        "decided": decided,
        "to_do": to_do,
        "waiting_on": waiting_on,
        "unclear": unclear,
        "needs_confirmation": needs_confirmation,
        "summary": {
            "decided_count": len(decided),
            "to_do_count": len(to_do),
            "waiting_on_count": len(waiting_on),
            "unclear_count": len(unclear),
            "needs_confirmation_count": len(needs_confirmation),
        },
    }


def project_pro(governed_state: dict[str, Any]) -> dict[str, Any]:
    """Project governed state into professional review cards."""
    promoted = governed_state.get("promoted", [])
    contested = governed_state.get("contested", [])
    deferred = governed_state.get("deferred", [])

    safe_to_rely_on = []
    still_uncertain = []
    needs_human_judgment = []
    commitments = []
    constraints = []
    evidence = []

    for claim in promoted:
        mother_type = claim.get("mother_type", "")
        text = claim.get("text", "")
        card = {"text": text, "mother_type": mother_type}

        if mother_type == "CONTRACT":
            commitments.append(card)
            safe_to_rely_on.append(card)
        elif mother_type == "CONSTRAINT":
            constraints.append(card)
            safe_to_rely_on.append(card)
        elif mother_type == "WITNESS":
            evidence.append(card)
            safe_to_rely_on.append(card)
        elif mother_type == "UNCERTAINTY":
            still_uncertain.append(card)
        else:
            safe_to_rely_on.append(card)

    for claim in contested:
        needs_human_judgment.append({"text": claim.get("text", ""), "reason": "contested"})

    for claim in deferred:
        needs_human_judgment.append({
            "text": claim.get("text", claim.get("claim_text", "")),
            "reason": claim.get("_defer_reason", "insufficient evidence"),
        })

    return {
        "safe_to_rely_on": safe_to_rely_on,
        "still_uncertain": still_uncertain,
        "needs_human_judgment": needs_human_judgment,
        "commitments": commitments,
        "constraints": constraints,
        "evidence": evidence,
        "contested": [{"text": c.get("text", "")} for c in contested],
        "summary": {
            "safe_count": len(safe_to_rely_on),
            "uncertain_count": len(still_uncertain),
            "judgment_count": len(needs_human_judgment),
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACTION_SIGNALS = frozenset({
    "need to", "should", "have to", "let's", "please", "make sure",
    "follow up", "send", "call", "schedule", "book",
    "pick up", "drop off", "confirm", "check",
    "remind", "update", "fix", "deploy", "ship",
})


def _fn_name(where: dict) -> str:
    """Extract a clean function name from a where anchor."""
    fn = where.get("function", "")
    if fn:
        return fn
    return where.get("text", "unknown")[:30]


def _violation_text(text: str) -> str:
    """Clean up violation text for rendering."""
    # Strip the "function:line — " prefix if present
    if " — " in text:
        return text.split(" — ", 1)[1]
    return text


def _looks_actionable(text: str) -> bool:
    """Heuristic: does this text look like something someone needs to do?"""
    lower = text.lower()
    return any(signal in lower for signal in _ACTION_SIGNALS)
