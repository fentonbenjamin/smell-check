"""Thread analysis pipeline — the engine.

Thread in → governed state out → receipt chain included.

This is the trunk pipeline:
  1. perceive — classify each turn's epistemic events
  2. type — map events into governance-typed units
  3. judge — promote, contest, defer, or lose claims through the sieve
  4. receipt — stamp every transform in the chain
  5. inscribe — durably store blobs, facts, and tx log entry

The canonical output is governed_state — not a projection.
Projections (consumer cards, pro cards) are lenses applied downstream.
"""

from __future__ import annotations

import re
from typing import Any

from .epistemic_tagger import classify_turn
from .mother_types import tagger_to_typed_units
from .sieve import promote
from .stamp import h, _canonical_json
from .receipted import run_pipeline_with_receipts
from .code_perception import detect_input_kind, analyzer_to_findings, diff_to_findings, split_mixed_input


def analyze_thread(
    text: str,
    *,
    topic_handle: str = "default",
    topic_keywords: set[str] | None = None,
    topic_description: str = "",
    actor: str = "",
    turn_id: str = "",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Analyze a thread and return governed state with receipt chain.

    This is the single entry point for the MVP.

    Args:
        text: Raw thread text (pasted, shared, or imported).
        topic_handle: Label for the topic/thread context.
        topic_keywords: Optional seed keywords. If None, inferred from text.
        topic_description: Optional topic description for the sieve.
        actor: Who produced this text (for provenance).
        turn_id: Optional turn identifier.
        db_path: If provided, inscribe the receipt to this SQLite store.

    Returns:
        {
            "governed_state": {
                "promoted": [...],
                "contested": [...],
                "deferred": [...],
                "loss": [...],
                "typed_units": [...],
                "classification": {...},
            },
            "receipt_chain": {
                "stamps": [...],
                "tagger_stamp": {...},
                "mother_type_stamp": {...},
                "sieve_stamp": {...},
            },
            "topic_context": {...},
        }
    """
    # Build topic context — minimal for MVP
    # If no keywords provided, infer from the text itself
    if topic_keywords is None:
        topic_keywords = _infer_keywords(text)

    topic_context: dict[str, Any] = {
        "handle": topic_handle,
        "keywords": topic_keywords,
        "description": topic_description,
    }

    # Detect input kind — route to the right perception lane
    input_kind = detect_input_kind(text)

    # Route to the right perception lane based on input kind.
    # When input is code/diff, ONLY the code lane runs.
    # The thread lane should not fire on docstrings and comments.

    if input_kind in ("python_source", "diff"):
        # CODE LANE ONLY — structural perception, no prose cues
        code_findings = []
        if input_kind == "python_source":
            code_findings = analyzer_to_findings(text)
        elif input_kind == "diff":
            code_findings = diff_to_findings(text)

        if code_findings:
            all_promoted, all_contested, all_deferred, all_loss = promote(
                code_findings, topic_context
            )
        else:
            all_promoted, all_contested, all_deferred, all_loss = [], [], [], []

        # Still need a receipted pipeline run for the stamp chain
        pipeline_result = run_pipeline_with_receipts(
            text, topic_context, turn_id=turn_id, actor=actor,
        )
        tagger_result = pipeline_result["tagger_result"]
        classification = tagger_result["classification"]
        typed_units = []

    elif input_kind == "mixed":
        # BOTH LANES — split input, run each lane on its segments
        segments = split_mixed_input(text)

        # Run receipted pipeline on FULL text (for correct blob→chain binding)
        # The tagger will see everything including code blocks, but that's OK —
        # the thread lane findings are prose-level and code findings come from
        # the code lane separately. Mixed input accepts that the tagger may
        # produce some noise on code blocks; the code lane's structural findings
        # will be stronger for those segments.
        pipeline_result = run_pipeline_with_receipts(
            text, topic_context, turn_id=turn_id, actor=actor,
        )
        tagger_result = pipeline_result["tagger_result"]
        sieve_result = pipeline_result["sieve_result"]
        classification = tagger_result["classification"]

        clause_spans = {c.clause_id: c.span for c in classification.clauses}
        tags_data = [
            {
                "event_type": t.event_type,
                "confidence": t.confidence,
                "span": t.span,
                "clause_id": t.clause_id,
                "source_span": clause_spans.get(t.clause_id),
            }
            for t in classification.tags
        ]
        typed_units = tagger_to_typed_units(
            text, tags_data, actor=actor, turn_id=turn_id,
        )

        thread_promoted = list(sieve_result["promoted"])
        thread_contested = list(sieve_result["contested"])
        thread_deferred = list(sieve_result["deferred"])
        thread_loss = list(sieve_result["loss"])

        # Code lane on code segments
        code_findings = []
        for code_seg in segments.get("code", []):
            # Strip fenced code block markers (```python ... ```)
            code_seg_clean = code_seg.strip()
            if code_seg_clean.startswith("```"):
                lines = code_seg_clean.split("\n")
                # Remove first line (```python) and last line (```)
                lines = [l for l in lines if not l.strip().startswith("```")]
                code_seg_clean = "\n".join(lines).strip()
            if not code_seg_clean:
                continue
            # Try as Python source first
            seg_findings = analyzer_to_findings(code_seg_clean)
            if not seg_findings:
                # Try as diff
                seg_findings = diff_to_findings(code_seg_clean)
            code_findings.extend(seg_findings)

        if code_findings:
            code_promoted, code_contested, code_deferred, code_loss = promote(
                code_findings, topic_context
            )
        else:
            code_promoted, code_contested, code_deferred, code_loss = [], [], [], []

        # Merge both lanes — anchor-aware dedup prevents cross-lane false collisions
        all_promoted = thread_promoted + list(code_promoted)
        all_contested = thread_contested + list(code_contested)
        all_deferred = thread_deferred + list(code_deferred)
        all_loss = thread_loss + list(code_loss)

    elif input_kind == "document":
        # DOCUMENT LANE — specs, plans, critiques, bug reports
        # The tagger runs but with filtering:
        # - suppress content inside code blocks (quoted examples, not assertions)
        # - suppress file paths and schema references
        # - require moderately higher confidence (0.5) to promote
        # - normative "should" in document prose is guidance, not an obligation
        pipeline_result = run_pipeline_with_receipts(
            text, topic_context, turn_id=turn_id, actor=actor,
        )
        tagger_result = pipeline_result["tagger_result"]
        sieve_result = pipeline_result["sieve_result"]
        classification = tagger_result["classification"]

        clause_spans = {c.clause_id: c.span for c in classification.clauses}
        tags_data = [
            {
                "event_type": t.event_type,
                "confidence": t.confidence,
                "span": t.span,
                "clause_id": t.clause_id,
                "source_span": clause_spans.get(t.clause_id),
            }
            for t in classification.tags
        ]

        # Filter tags: suppress quoted examples, paths, and low-confidence signals
        filtered_tags = []
        for tag in tags_data:
            span = tag.get("span", "")
            # Suppress content that looks like a quoted example or path
            if _is_document_noise(span):
                continue
            # Require higher confidence for document mode
            if tag.get("confidence", 0) < 0.5:
                continue
            filtered_tags.append(tag)

        typed_units = tagger_to_typed_units(
            text, filtered_tags, actor=actor, turn_id=turn_id,
        )

        # Re-promote with only the filtered units
        if typed_units:
            all_promoted, all_contested, all_deferred, all_loss = promote(
                typed_units, topic_context
            )
        else:
            all_promoted, all_contested, all_deferred, all_loss = [], [], [], []

    else:
        # THREAD LANE ONLY — clause cues, surface acts, prose perception
        pipeline_result = run_pipeline_with_receipts(
            text, topic_context, turn_id=turn_id, actor=actor,
        )
        tagger_result = pipeline_result["tagger_result"]
        sieve_result = pipeline_result["sieve_result"]
        classification = tagger_result["classification"]

        clause_spans = {c.clause_id: c.span for c in classification.clauses}
        tags_data = [
            {
                "event_type": t.event_type,
                "confidence": t.confidence,
                "span": t.span,
                "clause_id": t.clause_id,
                "source_span": clause_spans.get(t.clause_id),
            }
            for t in classification.tags
        ]
        typed_units = tagger_to_typed_units(
            text, tags_data, actor=actor, turn_id=turn_id,
        )

        all_promoted = sieve_result["promoted"]
        all_contested = sieve_result["contested"]
        all_deferred = sieve_result["deferred"]
        all_loss = sieve_result["loss"]

    governed_state = {
        "promoted": all_promoted,
        "contested": all_contested,
        "deferred": all_deferred,
        "loss": all_loss,
        "typed_units": [_unit_to_dict(u) for u in typed_units],
        "input_kind": input_kind,
        "classification": {
            "turn_id": classification.turn_id,
            "actor": classification.actor,
            "claim_count": classification.claim_count,
            "question_count": classification.question_count,
            "tags": [
                {"event_type": t.event_type, "confidence": t.confidence, "span": t.span}
                for t in classification.tags
            ],
        },
    }

    # Build receipt chain summary
    stamps = pipeline_result["stamps"]
    receipt_chain = {
        "stamps": [_stamp_to_dict(s) for s in stamps],
        "tagger_stamp": _stamp_to_dict(stamps[0]) if len(stamps) > 0 else None,
        "mother_type_stamp": _stamp_to_dict(stamps[1]) if len(stamps) > 1 else None,
        "sieve_stamp": _stamp_to_dict(stamps[2]) if len(stamps) > 2 else None,
        "chain_length": len(stamps),
        "tip_hash": stamps[-1].stamp_hash if stamps else None,
    }

    # Optionally inscribe to durable store
    if db_path:
        _inscribe(db_path, text, governed_state, receipt_chain, stamps)

    return {
        "governed_state": governed_state,
        "receipt_chain": receipt_chain,
        "topic_context": topic_context,
    }


def analyze_thread_multi(
    turns: list[dict[str, str]],
    *,
    topic_handle: str = "default",
    topic_keywords: set[str] | None = None,
    topic_description: str = "",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Analyze a thread with multiple turns.

    Each turn is a dict with at least "text" and optionally "actor".
    Returns aggregated governed state across all turns.

    Args:
        turns: List of {"text": "...", "actor": "..."} dicts.
        topic_handle: Label for the topic/thread context.
        topic_keywords: Optional seed keywords.
        topic_description: Optional topic description.
        db_path: If provided, inscribe each turn's receipt.

    Returns:
        Same shape as analyze_thread, but with aggregated state.
    """
    all_promoted = []
    all_contested = []
    all_deferred = []
    all_loss = []
    all_units = []
    all_stamps = []

    # Keywords accumulate across turns — the thread IS the context
    accumulated_keywords = set(topic_keywords) if topic_keywords else set()

    for i, turn in enumerate(turns):
        text = turn.get("text", "")
        actor = turn.get("actor", "")
        turn_id = turn.get("turn_id", f"turn_{i}")

        if not text.strip():
            continue

        result = analyze_thread(
            text,
            topic_handle=topic_handle,
            topic_keywords=accumulated_keywords,
            topic_description=topic_description,
            actor=actor,
            turn_id=turn_id,
            db_path=db_path,
        )

        gs = result["governed_state"]
        all_promoted.extend(gs["promoted"])
        all_contested.extend(gs["contested"])
        all_deferred.extend(gs["deferred"])
        all_loss.extend(gs["loss"])
        all_units.extend(gs["typed_units"])
        # Stamps are already dicts from analyze_thread
        all_stamps.extend(result["receipt_chain"]["stamps"])

        # Grow keywords from this turn's promoted claims
        for claim in gs["promoted"]:
            claim_text = claim.get("text", "")
            accumulated_keywords.update(_infer_keywords(claim_text))

    governed_state = {
        "promoted": all_promoted,
        "contested": all_contested,
        "deferred": all_deferred,
        "loss": all_loss,
        "typed_units": all_units,
        "classification": {"turn_count": len(turns)},
    }

    # all_stamps are already dicts (from analyze_thread's _stamp_to_dict)
    receipt_chain = {
        "stamps": all_stamps,
        "chain_length": len(all_stamps),
        "tip_hash": all_stamps[-1]["stamp_hash"] if all_stamps else None,
    }

    return {
        "governed_state": governed_state,
        "receipt_chain": receipt_chain,
        "topic_context": {
            "handle": topic_handle,
            "keywords": accumulated_keywords,
            "description": topic_description,
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _infer_keywords(text: str) -> set[str]:
    """Extract keywords from text for topic context bootstrapping.

    Uses the sieve's own keyword extraction logic.
    """
    from .sieve import _extract_keywords
    return _extract_keywords(text)


def _is_document_noise(span: str) -> bool:
    """Detect whether a tagger span is document noise rather than live signal.

    Document noise includes:
    - file paths (quality_corpus/families/...)
    - schema field references (fixture_id, expected_semantic)
    - code block content (```...```)
    - directory tree fragments (├── ── └──)
    - quoted examples from specs/critiques
    - meta-language headers (Expected:, Observed:, Repro:)
    """
    s = span.strip()

    # File paths
    if re.match(r"^[\w./-]+\.(py|rs|json|md|yaml|toml|swift|txt)\b", s):
        return True
    if s.count("/") >= 3:
        return True

    # Schema/fixture field names
    if re.match(r"^(fixture_id|expected_\w+|rubric_id|atlas_seed|gold_render|anti_gold)\b", s):
        return True

    # Directory tree fragments
    if any(c in s for c in ("├──", "└──", "│  ")):
        return True

    # Code block markers
    if s.startswith("```") or s.endswith("```"):
        return True

    # Very short fragments (likely bullet labels or field names)
    if len(s) < 15 and ":" in s:
        return True

    return False


def _unit_to_dict(unit: dict[str, Any]) -> dict[str, Any]:
    """Convert a typed unit to a clean dict, stripping transport fields."""
    # Keep semantic fields, drop nondeterministic transport
    skip = {"id", "_witness", "witness_refs", "schema_version"}
    return {k: v for k, v in unit.items() if k not in skip}


def _stamp_to_dict(stamp) -> dict[str, Any]:
    """Convert a Stamp dataclass to a dict."""
    return {
        "schema": stamp.schema,
        "domain": stamp.domain,
        "input_hash": stamp.input_hash,
        "fn_hash": stamp.fn_hash,
        "output_hash": stamp.output_hash,
        "prev_stamp_hash": stamp.prev_stamp_hash,
        "stamp_hash": stamp.stamp_hash,
    }


def _inscribe(
    db_path: str,
    text: str,
    governed_state: dict,
    receipt_chain: dict,
    stamps: list,
) -> None:
    """Inscribe the governed state and receipt chain to the store.

    input_data is the original thread text (what the transform consumed),
    NOT the governed_state (what the transform produced).
    """
    from .store import init_db, inscribe_receipt

    conn = init_db(db_path)

    # Input blob = the original text that entered the pipeline
    input_data = text.encode("utf-8") if isinstance(text, str) else text
    # Output blob = the full governed state (what the pipeline produced)
    output_data = _canonical_json(governed_state).encode()

    # Use the last stamp in the chain (sieve stamp) as the receipt
    if stamps:
        facts = []
        for i, claim in enumerate(governed_state.get("promoted", [])):
            facts.append({
                "id": f"fact_{stamps[-1].stamp_hash[:8]}_{i}",
                "domain": "semantic",
                "type": claim.get("mother_type", "CONTRACT"),
                "content": _canonical_json(claim),
            })

        inscribe_receipt(
            conn,
            stamps[-1],
            input_data=input_data,
            output_data=output_data,
            facts=facts,
        )

    conn.close()
