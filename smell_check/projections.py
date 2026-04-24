"""Projection lenses — governed state → human-readable smell check output.

Pipeline: perceive → promote → type → compress → render

The governed state is the canonical object. Projections are lenses over it.
Same engine, different leaves.

The output reads like a sharp review:
  - one judgment per concern, not one card per claim
  - evidence behind the card, not as the card
  - short normalized judgments, not transcript fragments
"""

from __future__ import annotations

import re
import string
from typing import Any


# ---------------------------------------------------------------------------
# Step 1: Type claims into judgment categories
# ---------------------------------------------------------------------------

# Judgment types — stricter than mother types
STABLE = "stable"           # genuinely settled proposition
RISK = "risk"               # concern that limits confidence
ACTION = "action"           # someone needs to do something
OPEN_Q = "open_question"    # explicit or tightly inferred unresolved
META = "meta"               # process commentary — suppress in output


def _type_claim(claim: dict[str, Any]) -> str:
    """Assign a judgment type to a promoted claim. Pure."""
    mother_type = claim.get("mother_type", "")
    text = claim.get("text", "")
    lower = text.lower()
    finding_kind = claim.get("_finding_kind", "")
    event = claim.get("epistemic_event", "")

    # Code findings keep their existing classification
    if finding_kind:
        if finding_kind in ("purity", "guard_present"):
            return STABLE
        return RISK

    # Challenge/question → open question
    _challenge_cues = (
        "i thought we", "didn't we", "wasn't that",
        "but we", "wait,", "hold on",
    )
    if mother_type == "UNCERTAINTY":
        return OPEN_Q
    if mother_type == "CONTRACT" and (
        "?" in text
        or any(cue in lower for cue in _challenge_cues)
        or event == "belief_revised"
    ):
        return OPEN_Q

    # Actionable constraints → action or risk
    if mother_type == "CONSTRAINT":
        if _looks_actionable(text):
            return ACTION
        return RISK

    # Contested → risk
    if claim.get("_contested"):
        return RISK

    # Agreement / decision → stable
    _agreement_cues = (
        "we decided", "we agreed", "decided to", "the plan is",
        "confirmed", "locked in", "set for",
        "agreed", "makes sense", "sounds good", "perfect",
        "let's go with", "let's do", "will do",
    )
    if mother_type == "CONTRACT" and any(cue in lower for cue in _agreement_cues):
        return STABLE

    # WITNESS → stable (reported fact)
    if mother_type == "WITNESS":
        return STABLE

    # RELATION → meta (suppress "Noted:" cards)
    if mother_type == "RELATION":
        # Only promote to stable if it has real substance (>60 chars, specific)
        if len(text) > 60 and event == "tension_resolved":
            return STABLE
        return META

    # CONTRACT without agreement cues → stable only if substantial
    if mother_type == "CONTRACT":
        if _looks_actionable(text):
            return ACTION
        if len(text) > 40:
            return STABLE
        return META

    return META


# ---------------------------------------------------------------------------
# Step 2: Normalize judgment text
# ---------------------------------------------------------------------------

# Speaker attribution pattern: "Alice:", "Bob:", "PM:", "Dev A:", "Eng Lead:"
_SPEAKER_RE = re.compile(r"^[A-Z][A-Za-z\s]*:\s*")

# Hedge prefixes to strip
_HEDGE_PREFIXES = (
    "i think ", "i believe ", "i feel like ", "maybe ",
    "probably ", "i guess ", "well, ",
)


def _normalize_text(text: str) -> str:
    """Strip speaker attribution, hedge prefixes, and clean up for display. Pure."""
    t = text.strip()
    # Strip speaker attribution
    t = _SPEAKER_RE.sub("", t)
    # Strip hedge prefixes
    lower = t.lower()
    for prefix in _HEDGE_PREFIXES:
        if lower.startswith(prefix):
            t = t[len(prefix):]
            t = t[0].upper() + t[1:] if t else t
            break
    # Trim trailing period if it's the only one (not an abbreviation)
    if t.endswith(".") and t.count(".") == 1:
        t = t[:-1]
    return t.strip()


def _normalize_judgment(jtype: str, text: str) -> str:
    """Create a clean judgment sentence from claim text. Pure."""
    clean = _normalize_text(text)
    if not clean:
        return text

    # Truncate long transcript fragments
    if len(clean) > 120:
        # Find a natural break point
        for sep in (". ", "; ", ", ", " — "):
            idx = clean.find(sep, 40)
            if 40 < idx < 100:
                clean = clean[:idx]
                break
        else:
            clean = clean[:100] + "..."

    return clean


# ---------------------------------------------------------------------------
# Step 3: Compress — cluster related judgments, dedupe, pick strongest
# ---------------------------------------------------------------------------

def _extract_words(text: str) -> set[str]:
    """Extract meaningful words for clustering."""
    _stop = frozenset({
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "to", "for", "of", "in", "on", "and", "or", "but", "not",
        "with", "at", "by", "from", "as", "it", "its", "this", "that",
        "we", "i", "you", "they", "he", "she", "do", "does", "did",
        "will", "would", "could", "should", "can", "has", "have", "had",
        "so", "if", "then", "just", "also", "very", "too", "really",
    })
    stripped = text.lower().translate(str.maketrans("", "", string.punctuation))
    return set(stripped.split()) - _stop


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _compress_group(items: list[dict[str, Any]], threshold: float = 0.4) -> list[dict[str, Any]]:
    """Cluster related items and pick one representative per cluster. Pure."""
    if not items:
        return []

    clusters: list[list[dict[str, Any]]] = []
    cluster_words: list[set[str]] = []

    for item in items:
        words = _extract_words(item.get("judgment", ""))
        placed = False
        for i, cw in enumerate(cluster_words):
            if _jaccard(words, cw) >= threshold:
                clusters[i].append(item)
                cluster_words[i] |= words
                placed = True
                break
        if not placed:
            clusters.append([item])
            cluster_words.append(words)

    # Pick representative: prefer longest judgment in each cluster
    result = []
    for cluster in clusters:
        rep = max(cluster, key=lambda x: len(x.get("judgment", "")))
        # Attach evidence from other cluster members
        evidence = []
        for item in cluster:
            if item is not rep:
                evidence.append(item.get("where", {}).get("text", ""))
        if evidence:
            rep = dict(rep)  # copy
            rep["supporting_evidence"] = [e for e in evidence if e]
        result.append(rep)

    return result


# ---------------------------------------------------------------------------
# Step 4: Render — compose the final output
# ---------------------------------------------------------------------------

def project_smell_check(governed_state: dict[str, Any]) -> dict[str, Any]:
    """Project governed state into smell check output.

    Pipeline: type → normalize → compress → render
    One concern → one judgment card → supporting evidence behind it.
    """
    promoted = governed_state.get("promoted", [])
    contested = governed_state.get("contested", [])
    deferred = governed_state.get("deferred", [])
    input_kind = governed_state.get("input_kind", "thread")

    # --- Type each claim ---
    typed: dict[str, list[dict[str, Any]]] = {
        STABLE: [], RISK: [], ACTION: [], OPEN_Q: [], META: [],
    }

    for claim in promoted:
        jtype = _type_claim(claim)
        text = claim.get("text", "")
        finding_kind = claim.get("_finding_kind", "")

        # Build the source anchor
        code_where = claim.get("_where")
        source_span = claim.get("source_span")
        clause_id = claim.get("clause_id", "")
        if code_where:
            where = dict(code_where)
            where["text"] = text
        else:
            where = {"text": text, "clause_id": clause_id}
            if source_span:
                where["char_offset"] = source_span

        drillback = {
            "mother_type": claim.get("mother_type", ""),
            "subtype": claim.get("subtype", ""),
            "confidence": claim.get("confidence"),
            "epistemic_event": claim.get("epistemic_event", ""),
        }

        # Code findings use existing specialized rendering
        if finding_kind:
            card = _render_code_finding(claim, finding_kind, where, drillback)
            if card:
                typed[jtype].append(card)
                continue

        # Thread/document claims: normalize the judgment text
        judgment = _normalize_judgment(jtype, text)

        card = {
            "judgment": judgment,
            "where": where,
            "drillback": drillback,
        }

        # Type-specific framing
        if jtype == RISK:
            card["because"] = _risk_reason(claim)
            card["what_to_do"] = _risk_action(claim)
        elif jtype == ACTION:
            card["because"] = "This is an obligation or required action."
            card["what_to_do"] = "Assign it or confirm it's handled."
        elif jtype == OPEN_Q:
            card["because"] = _question_reason(claim)
            card["what_to_do"] = "Resolve before relying on it."
        elif jtype == STABLE:
            card["because"] = _stable_reason(claim)

        typed[jtype].append(card)

    # Contested → risks
    for claim in contested:
        text = claim.get("text", "")
        typed[RISK].append({
            "judgment": _normalize_text(text),
            "because": "Conflicting signals — contested by other evidence.",
            "where": {"text": text, "clause_id": claim.get("clause_id", "")},
            "what_to_do": "Resolve the conflict before relying on it.",
            "drillback": {"status": "contested"},
        })

    # Deferred → weak open questions (only if substantive)
    for claim in deferred:
        text = claim.get("text", claim.get("claim_text", ""))
        if len(text.strip()) < 20:
            continue  # too thin to surface
        reason = claim.get("_defer_reason", "insufficient evidence")
        typed[OPEN_Q].append({
            "judgment": _normalize_text(text),
            "because": f"Deferred — {reason}.",
            "where": {"text": text, "clause_id": claim.get("clause_id", "")},
            "what_to_do": "Gather more evidence or clarify.",
            "drillback": {"status": "deferred", "reason": reason},
        })

    # --- Compress each group ---
    findings = _compress_group(typed[RISK] + typed[ACTION])
    stable_points = _compress_group(typed[STABLE])
    open_questions = _compress_group(typed[OPEN_Q])
    # META is suppressed — not rendered

    # --- Build summary ---
    total_issues = len(findings) + len(open_questions)
    if total_issues == 0 and stable_points:
        summary = "Everything looks stable. No smells detected."
    elif total_issues == 0:
        summary = "Not enough signal to judge. Try a longer thread."
    else:
        parts = []
        if findings:
            parts.append(f"{len(findings)} concern{'s' if len(findings) != 1 else ''}")
        if open_questions:
            parts.append(f"{len(open_questions)} open question{'s' if len(open_questions) != 1 else ''}")
        summary = f"{'. '.join(parts)}."
        if stable_points:
            summary += f" {len(stable_points)} point{'s' if len(stable_points) != 1 else ''} look{'s' if len(stable_points) == 1 else ''} stable."

    return {
        "summary": summary,
        "findings": findings,
        "stable_points": stable_points,
        "open_questions": open_questions,
    }


# ---------------------------------------------------------------------------
# Code finding rendering (unchanged — these are already structural)
# ---------------------------------------------------------------------------

def _render_code_finding(
    claim: dict[str, Any],
    finding_kind: str,
    where: dict[str, Any],
    drillback: dict[str, Any],
) -> dict[str, Any] | None:
    """Render a code-lane finding. Returns None to skip."""
    text = claim.get("text", "")
    signals = claim.get("_signals", [])

    if finding_kind == "impurity":
        signal_detail = f": {', '.join(signals)}" if signals else ""
        return {
            "judgment": f"{_fn_name(where)} has side effects{signal_detail}",
            "because": "AST analysis found I/O or impure operations.",
            "where": where,
            "what_to_do": "Review whether the impurity is intentional.",
            "drillback": drillback,
        }
    elif finding_kind == "violation":
        return {
            "judgment": f"{_fn_name(where)}: {_violation_text(text)}",
            "because": "Structural analysis found a constraint violation.",
            "where": where,
            "what_to_do": "Review the function's behavior vs its contract.",
            "drillback": drillback,
        }
    elif finding_kind == "exception_safety":
        return {
            "judgment": f"{_fn_name(where)}: {_violation_text(text)}",
            "because": "Exception handling issue detected by AST.",
            "where": where,
            "what_to_do": "Add proper error handling.",
            "drillback": drillback,
        }
    elif finding_kind == "guard_present":
        suffix = text.split(" has ", 1)[1] if " has " in text else "has validation"
        return {
            "judgment": f"{_fn_name(where)} {suffix}",
            "because": "Input validation guards detected by AST.",
            "where": where,
            "drillback": drillback,
        }
    elif finding_kind == "purity":
        return {
            "judgment": f"{_fn_name(where)} is structurally pure",
            "because": "No I/O, no side effects, no global state.",
            "where": where,
            "drillback": drillback,
        }
    elif finding_kind == "global_mutation":
        return {
            "judgment": _normalize_text(text),
            "because": "Module-level mutable state modified.",
            "where": where,
            "what_to_do": "Consider passing state explicitly.",
            "drillback": drillback,
        }
    elif finding_kind == "guard_removed":
        return {
            "judgment": f"Guard removed: {_normalize_text(text)}",
            "because": "Validation or safety check was removed.",
            "where": where,
            "what_to_do": "Verify the removal was intentional and safe.",
            "drillback": drillback,
        }
    elif finding_kind == "error_path_changed":
        return {
            "judgment": f"Error handling changed: {_normalize_text(text)}",
            "because": "try/except/raise/logging lines were modified.",
            "where": where,
            "what_to_do": "Review the error path for correctness.",
            "drillback": drillback,
        }
    elif finding_kind == "test_gap":
        return {
            "judgment": f"No test delta: {_normalize_text(text)}",
            "because": "Behavior changed but no test file was modified.",
            "where": where,
            "what_to_do": "Add or update tests.",
            "drillback": drillback,
        }
    elif finding_kind == "provenance_gap":
        return {
            "judgment": f"External dependency: {_normalize_text(text)}",
            "because": "This import appears external to the repo.",
            "where": where,
            "what_to_do": "Verify the dependency source.",
            "drillback": drillback,
        }
    elif finding_kind in ("significant_removal", "large_addition", "file_change"):
        return {
            "judgment": _normalize_text(text),
            "because": f"Diff structural signal: {finding_kind.replace('_', ' ')}.",
            "where": where,
            "what_to_do": "Review the change.",
            "drillback": drillback,
        }
    return None


# ---------------------------------------------------------------------------
# Reason generators (replaces hardcoded prefix strings)
# ---------------------------------------------------------------------------

def _risk_reason(claim: dict[str, Any]) -> str:
    mother = claim.get("mother_type", "")
    if mother == "CONSTRAINT":
        return "Expressed as a constraint or requirement."
    if claim.get("_contested"):
        return "Conflicting signals in the thread."
    return "This concern has not been fully resolved."


def _risk_action(claim: dict[str, Any]) -> str:
    mother = claim.get("mother_type", "")
    if mother == "CONSTRAINT" and _looks_actionable(claim.get("text", "")):
        return "Assign it or confirm it's handled."
    return "Verify this is being addressed."


def _question_reason(claim: dict[str, Any]) -> str:
    event = claim.get("epistemic_event", "")
    if event == "belief_revised":
        return "This challenges or revises a prior position."
    if event == "question_posed":
        return "Explicitly raised as a question."
    if "?" in claim.get("text", ""):
        return "Posed as a question in the thread."
    return "Expressed as uncertainty."


def _stable_reason(claim: dict[str, Any]) -> str:
    event = claim.get("epistemic_event", "")
    mother = claim.get("mother_type", "")
    if event == "tension_resolved":
        return "A prior concern was explicitly resolved."
    if mother == "WITNESS":
        return "Attributed to a source or observation."
    lower = claim.get("text", "").lower()
    if any(cue in lower for cue in ("agreed", "decided", "confirmed", "sounds good")):
        return "Expressed as agreement or decision."
    return "Expressed as a settled proposition."


# ---------------------------------------------------------------------------
# Legacy projections (unchanged)
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
    fn = where.get("function", "")
    if fn:
        return fn
    return where.get("text", "unknown")[:30]


def _violation_text(text: str) -> str:
    if " — " in text:
        return text.split(" — ", 1)[1]
    return text


def _looks_actionable(text: str) -> bool:
    lower = text.lower()
    # Use word boundary matching — "checkout" should not match "check"
    for signal in _ACTION_SIGNALS:
        if " " in signal:
            # Multi-word: substring is fine ("need to", "make sure")
            if signal in lower:
                return True
        else:
            # Single word: require word boundary
            if re.search(r'\b' + re.escape(signal) + r'\b', lower):
                return True
    return False
