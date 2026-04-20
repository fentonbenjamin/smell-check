"""Sieve — purely functional claim promotion and structuring.

This is the IP. promote() and structure() are PURE FUNCTIONS.
No LLM calls. No network. No side effects. Same input -> same output, every time.

Stages:
  1. promote(claims, topic_context) -> (promoted, loss)
     Relevance filter, type accuracy, dedup, cross-contamination.
  2. structure(promoted, topic_context) -> TopicNarrative
     Group by type, build summary, enforce compression.
  3. sieve_topic(store, handle) -> dict
     Orchestrator that loads data, calls pure stages, persists result.
"""

from __future__ import annotations

import string
from typing import Any



# ---------------------------------------------------------------------------
# Stopwords for keyword extraction
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "for", "of", "in", "on", "and", "or", "but", "not", "with",
    "at", "by", "from", "as", "into", "through", "about", "between",
    "it", "its", "this", "that", "these", "those", "he", "she", "they",
    "we", "you", "i", "my", "your", "our", "their", "his", "her",
    "do", "does", "did", "will", "would", "could", "should", "can",
    "may", "might", "shall", "has", "have", "had",
    "so", "if", "then", "than", "no", "yes", "all", "some", "any",
    "each", "every", "both", "few", "more", "most", "other", "such",
    "what", "which", "who", "whom", "how", "when", "where", "why",
    "up", "out", "off", "over", "under", "again", "once", "here", "there",
    "just", "also", "very", "too", "quite", "really", "only",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text. Pure function.

    Keeps compound terms like 'pure function', 'sieve pipeline' as individual
    words but also preserves multi-word concepts via bigrams when the words
    are non-stopwords adjacent in the original text.
    """
    stripped = text.lower().translate(str.maketrans("", "", string.punctuation))
    words = set(stripped.split()) - _STOPWORDS

    # Extract bigrams from non-stopword adjacent pairs for compound concepts
    raw_words = stripped.split()
    for i in range(len(raw_words) - 1):
        a, b = raw_words[i], raw_words[i + 1]
        if a not in _STOPWORDS and b not in _STOPWORDS:
            words.add(f"{a} {b}")

    return words


def _infer_topic_terms(claims: list[dict[str, Any]], threshold: int = 3, bigram_threshold: int = 2) -> set[str]:
    """Infer topic-relevant terms from claim corpus. Pure function.

    Terms (single words, excluding stopwords) that appear in ``threshold``+
    claims are likely topic-relevant concepts.  Additionally, 2-word phrases
    where both words are non-stopwords and the bigram appears in
    ``bigram_threshold``+ claims are included. This catches compound concepts
    like "pure function", "sieve pipeline", "topic funnel", "evidence refs".
    """
    from collections import Counter
    term_counts: Counter[str] = Counter()
    bigram_counts: Counter[str] = Counter()
    for c in claims:
        text = c.get("text", "")
        claim_words = text.lower().translate(str.maketrans("", "", string.punctuation)).split()
        unique_words = set(claim_words) - _STOPWORDS
        for w in unique_words:
            term_counts[w] += 1

        # Bigrams: adjacent non-stopword pairs (deduplicated per claim)
        claim_bigrams: set[str] = set()
        for i in range(len(claim_words) - 1):
            a, b = claim_words[i], claim_words[i + 1]
            if a not in _STOPWORDS and b not in _STOPWORDS:
                claim_bigrams.add(f"{a} {b}")
        for bg in claim_bigrams:
            bigram_counts[bg] += 1

    result = {term for term, count in term_counts.items() if count >= threshold}
    result |= {bg for bg, count in bigram_counts.items() if count >= bigram_threshold}
    return result


_STRUCTURAL_MARKERS = ("##", "**", "- ", "```", "| ", "1. ", "2. ", "3. ", "4. ", "5. ")

_NEGATION_WORDS = frozenset({
    "not", "don't", "doesn't", "isn't", "aren't", "can't", "cannot",
    "won't", "never", "no", "neither", "nor",
})


def _has_attack_signal(text_a: str, text_b: str) -> bool:
    """Detect if two claims have a contradiction/attack signal. Pure function.

    Returns True if one claim contains negation words near shared key terms
    with the other claim, suggesting they are in tension.
    """
    kw_a = _extract_keywords(text_a)
    kw_b = _extract_keywords(text_b)
    shared = kw_a & kw_b
    if len(shared) < 2:
        return False

    words_a = text_a.lower().translate(str.maketrans("", "", string.punctuation)).split()
    words_b = text_b.lower().translate(str.maketrans("", "", string.punctuation)).split()

    for words in (words_a, words_b):
        neg_pos = [i for i, w in enumerate(words) if w in _NEGATION_WORDS]
        term_pos = [i for i, w in enumerate(words) if w in shared]
        for np_ in neg_pos:
            for tp in term_pos:
                if abs(np_ - tp) <= 4:
                    return True
    return False


def _is_structural_fragment(text: str) -> bool:
    """Detect markdown structural fragments (frameworks, tables, lists). Pure function."""
    return any(marker in text for marker in _STRUCTURAL_MARKERS)


def _normalize_words(text: str) -> set[str]:
    """Normalize text to word set for dedup comparison."""
    stripped = text.lower().translate(str.maketrans("", "", string.punctuation))
    return set(stripped.split())


def _dedup_anchor(claim: dict[str, Any]) -> str | None:
    """Extract a structural dedup anchor from a claim. Pure.

    Code findings: (file, function, finding_kind)
    Thread findings: clause_id if present

    Returns None if no anchor available (falls back to word overlap).
    """
    where = claim.get("_where")
    finding_kind = claim.get("_finding_kind", "")

    if where and isinstance(where, dict):
        # Code finding with file/function/line anchor
        f = where.get("file", "")
        fn = where.get("function", "")
        if f or fn:
            return f"{f}:{fn}:{finding_kind}"

    clause_id = claim.get("clause_id", "")
    if clause_id and finding_kind:
        return f"{clause_id}:{finding_kind}"

    return None


def _word_overlap(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two word sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Stage 1: promote — PURE FUNCTION
# ---------------------------------------------------------------------------

# Patterns that indicate a question, not a fact
_QUESTION_SIGNALS = ("should we", "how do", "what if", "how can", "how should")

# Patterns that indicate observation/feeling, not fact
_OBSERVATION_SIGNALS = ("i feel", "i think", "i want", "i can't", "i just")


def promote(
    claims: list[dict[str, Any]],
    topic_context: dict[str, Any],
    all_topic_handles: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Promote claims through the sieve. Pure function.

    Args:
        claims: list of dicts with at minimum 'text' and 'claim_type'
        topic_context: dict with 'handle', 'title', 'description', 'keywords'
        all_topic_handles: optional list of all active topic handles
                           (for cross-contamination detection)

    Returns:
        (promoted_claims, contested_claims, deferred_claims, loss_declarations)

        Contested claims: pairs that attack each other, both kept, marked as in tension.
        Deferred claims pass relevance but lack confidence signals:
        - No evidence_refs AND no confidence score
        - Very short (< 10 chars)
        - claim_type is empty or "unknown"
    """
    keywords = topic_context.get("keywords", set())
    if isinstance(keywords, list):
        keywords = set(keywords)
    handle = topic_context.get("handle", "")
    other_handles = set(all_topic_handles or []) - {handle}

    promoted: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    loss: list[dict[str, Any]] = []

    # Working list: apply type accuracy first, then filter
    reclassified: list[dict[str, Any]] = []

    # Mother type constants (inline to avoid circular import)
    _MOTHER_TYPES = frozenset({"CONTRACT", "CONSTRAINT", "UNCERTAINTY", "RELATION", "WITNESS"})

    for claim in claims:
        c = dict(claim)  # shallow copy to avoid mutating input
        text = c.get("text", "").strip()
        claim_type = c.get("claim_type", "")
        mother_type = c.get("mother_type", "")
        lower_text = text.lower()

        # --- (b) Type accuracy check ---
        # If mother_type is set (from TypedUnit v0), trust it over claim_type
        if mother_type in _MOTHER_TYPES:
            # Mother type is authoritative — sync claim_type for compat
            _mt_to_ct = {
                "CONTRACT": "fact", "CONSTRAINT": "constraint",
                "UNCERTAINTY": "hypothesis", "RELATION": "observation",
                "WITNESS": "guarantee",
            }
            if not claim_type or claim_type not in ("fact", "constraint", "hypothesis", "observation", "guarantee"):
                c["claim_type"] = _mt_to_ct.get(mother_type, "fact")
        else:
            # Legacy path: reclassify surrogates
            if claim_type == "fact":
                if text.startswith("?") or any(sig in lower_text for sig in _QUESTION_SIGNALS):
                    c["claim_type"] = "question"
                    c["mother_type"] = "UNCERTAINTY"
                    c["_reclassified_from"] = "fact"
                elif any(sig in lower_text for sig in _OBSERVATION_SIGNALS):
                    c["claim_type"] = "observation"
                    c["mother_type"] = "RELATION"
                    c["_reclassified_from"] = "fact"

            if claim_type == "hypothesis":
                hedge_words = {"may", "might", "could", "possibly", "perhaps", "likely",
                               "probably", "seems", "appears", "suggests", "hypothes",
                               "if ", "whether", "maybe"}
                if not any(hw in lower_text for hw in hedge_words):
                    c["claim_type"] = "claim"
                    c["mother_type"] = "CONTRACT"
                    c["_reclassified_from"] = "hypothesis"

        reclassified.append(c)

    # --- (a) Relevance filter ---
    # Stronger signals first, lexical as fallback (per Codex review)
    handle_words = _extract_keywords(handle.replace("-", " "))
    description = topic_context.get("description", "")
    desc_keywords = _extract_keywords(description) if description else set()
    combined_keywords = keywords | handle_words | desc_keywords

    # Concept expansion: infer topic terms from claim corpus
    inferred_terms = _infer_topic_terms(reclassified, threshold=3)
    expanded_keywords = combined_keywords | inferred_terms

    relevant: list[dict[str, Any]] = []
    for c in reclassified:
        text = c.get("text", "")
        mother_type = c.get("mother_type", "")
        claim_words = _extract_keywords(text)

        # Signal 0 (highest priority): typed unit with explicit mother type
        # CONSTRAINT and WITNESS are always relevant — they are governance signals
        # UNCERTAINTY is always relevant — it's an honest declaration
        if mother_type in ("CONSTRAINT", "WITNESS", "UNCERTAINTY"):
            relevant.append(c)
            continue

        # Signal 0b: typed unit with a non-unknown subtype = intentionally classified
        subtype = c.get("subtype", "")
        if mother_type in _MOTHER_TYPES and subtype and subtype != "unknown_subtype":
            relevant.append(c)
            continue

        # Signal 1: source is from a thread linked to this topic
        source = c.get("source", "")
        if handle in source:
            relevant.append(c)
            continue

        # Signal 2 (removed): evidence_refs was auto-promoting without
        # verifying refs belong to THIS topic. That's a bypass vulnerability.
        # Evidence refs are now just metadata, not a relevance signal.

        # Signal 3: provenance chain mentions the topic
        provenance = str(c.get("provenance", ""))
        if handle in provenance:
            relevant.append(c)
            continue

        # Signal 4 (fallback): lexical keyword overlap (with expanded terms)
        # Check single-word overlap first
        single_word_overlap = claim_words & expanded_keywords
        if single_word_overlap:
            relevant.append(c)
            continue

        loss.append({
            "claim_text": text,
            "reason": "not relevant to topic",
            "rule_applied": "relevance_filter",
        })

    # --- (d) Cross-contamination filter ---
    after_cross: list[dict[str, Any]] = []
    # Skip very short handles (< 3 chars) -- too generic to be meaningful signals
    meaningful_other_handles = {h for h in other_handles if len(h) >= 3}
    for c in relevant:
        text = c.get("text", "")
        lower_text = text.lower()
        claim_words = _extract_keywords(text)
        handle_words = _extract_keywords(handle.replace("-", " "))
        combined_keywords_local = keywords | handle_words | desc_keywords

        # Check if claim mentions another topic's handle
        mentioned_other = None
        for other_h in meaningful_other_handles:
            if other_h.lower() in lower_text:
                mentioned_other = other_h
                break

        if mentioned_other:
            # If ONLY relates to other topic (no current-topic keywords
            # beyond the other handle's words), drop it
            other_kw = _extract_keywords(mentioned_other.replace("-", " "))
            current_topic_overlap = (claim_words & combined_keywords_local) - other_kw
            if not current_topic_overlap:
                loss.append({
                    "claim_text": text,
                    "reason": f"belongs to topic {mentioned_other}",
                    "rule_applied": "cross_contamination_filter",
                })
                continue

        after_cross.append(c)

    # --- (c) Deduplication ---
    kept: list[dict[str, Any]] = []
    word_sets: list[tuple[dict[str, Any], set[str]]] = []

    for c in after_cross:
        text = c.get("text", "")
        c_words = _normalize_words(text)
        is_dup = False

        # Anchor-aware dedup: code findings use structural identity, not word overlap.
        # Two findings about different functions are not duplicates even if they share words.
        c_anchor = _dedup_anchor(c)

        for i, (existing, existing_words) in enumerate(word_sets):
            # If both have anchors, compare anchors first
            e_anchor = _dedup_anchor(existing)
            if c_anchor and e_anchor:
                if c_anchor != e_anchor:
                    continue  # different anchor = not a duplicate, skip word check

            overlap = _word_overlap(c_words, existing_words)
            if overlap > 0.8:
                # Keep the longer one
                existing_text = existing.get("text", "")
                if len(text) > len(existing_text):
                    # Replace existing with current (longer)
                    loss.append({
                        "claim_text": existing_text,
                        "reason": f"duplicate of {text[:50]}",
                        "rule_applied": "deduplication",
                    })
                    word_sets[i] = (c, c_words)
                    # Replace in kept list
                    for j, k in enumerate(kept):
                        if k.get("text", "") == existing_text:
                            kept[j] = c
                            break
                else:
                    loss.append({
                        "claim_text": text,
                        "reason": f"duplicate of {existing_text[:50]}",
                        "rule_applied": "deduplication",
                    })
                is_dup = True
                break

        if not is_dup:
            kept.append(c)
            word_sets.append((c, c_words))

    # --- (e) Deferred bucket ---
    # Claims that pass relevance but lack confidence signals.
    # Structural fragments and long substantive text bypass deferral.
    final_promoted: list[dict[str, Any]] = []
    for c in kept:
        text = c.get("text", "").strip()
        claim_type = c.get("claim_type", "")
        evidence_refs = c.get("evidence_refs", [])
        confidence = c.get("confidence")

        # Structural fragments are substantive — never defer, and assign
        # a synthetic type if the original is empty.
        structural = _is_structural_fragment(text)
        if structural and (not claim_type or claim_type.lower() == "unknown"):
            c["claim_type"] = "framework_fragment"
            claim_type = "framework_fragment"

        is_deferred = False
        defer_reason = ""
        mother_type = c.get("mother_type", "")

        # Governance types are never deferred — they ARE the honest declarations
        # CONSTRAINT, WITNESS, UNCERTAINTY should always advance
        if mother_type in ("CONSTRAINT", "WITNESS", "UNCERTAINTY"):
            final_promoted.append(c)
            continue

        # Typed units with explicit subtypes bypass deferral
        subtype = c.get("subtype", "")
        if mother_type in _MOTHER_TYPES and subtype and subtype != "unknown_subtype":
            final_promoted.append(c)
            continue

        # Very short claims lack substance (lowered from 20 to 10)
        if len(text) < 10:
            is_deferred = True
            defer_reason = "too short (< 10 chars)"
        # Unknown or empty type means the sieve can't classify
        # (but structural fragments already got a type above)
        elif not claim_type or claim_type.lower() == "unknown":
            is_deferred = True
            defer_reason = "claim_type undetermined"
        # No evidence and no confidence = no signal
        # UNLESS: text is long (>100 chars) — the text itself IS evidence
        # UNLESS: it's a structural fragment — frameworks are substantive
        elif not evidence_refs and confidence is None:
            if len(text) > 100 or structural:
                pass  # promote anyway
            else:
                is_deferred = True
                defer_reason = "no evidence_refs and no confidence"

        if is_deferred:
            c["_defer_reason"] = defer_reason
            deferred.append(c)
        else:
            final_promoted.append(c)

    # --- (f) Contested detection ---
    # Find attack pairs among promoted claims. Both survive, marked as contested.
    contested: list[dict[str, Any]] = []
    contested_ids: set[int] = set()

    for i in range(len(final_promoted)):
        for j in range(i + 1, len(final_promoted)):
            # Anchor-aware contest: different anchors cannot contest each other
            anchor_i = _dedup_anchor(final_promoted[i])
            anchor_j = _dedup_anchor(final_promoted[j])
            if anchor_i and anchor_j and anchor_i != anchor_j:
                continue  # different structural anchors = not in tension

            text_i = final_promoted[i].get("text", "").strip()
            text_j = final_promoted[j].get("text", "").strip()
            if _has_attack_signal(text_i, text_j):
                # Both go to contested — neither silently demoted
                contested_ids.add(i)
                contested_ids.add(j)

    if contested_ids:
        new_promoted = []
        for i, c in enumerate(final_promoted):
            if i in contested_ids:
                c["_contested"] = True
                contested.append(c)
            else:
                new_promoted.append(c)
        final_promoted = new_promoted

    return final_promoted, contested, deferred, loss


# ---------------------------------------------------------------------------
# Stage 2: structure — PURE FUNCTION
# ---------------------------------------------------------------------------
# Stage 2b: synthesize — PURE FUNCTION
# ---------------------------------------------------------------------------


def synthesize(
    promoted: list[dict[str, Any]],
    cluster_threshold: float = 0.3,
    max_lines: int = 7,
    representative: str = "longest",
) -> dict[str, Any]:
    """Compress promoted claims into a synopsis. Pure function.

    Clusters claims by word overlap, picks one representative per cluster,
    ranks clusters by size. Same input → same synopsis.

    Args:
        promoted: list of promoted claim dicts (from promote())
        cluster_threshold: Jaccard overlap to group claims (0.0-1.0)
            Lower = more clustering = fewer synopsis lines
            Higher = less clustering = more synopsis lines
        max_lines: max number of representative claims in synopsis
        representative: how to pick the cluster rep
            "longest" = longest text
            "first" = first encountered

    Returns:
        {
            "synopsis_lines": [str, ...],
            "clusters": [{representative, members, size}, ...],
            "params": {cluster_threshold, max_lines, representative},
            "input_count": int,
            "synopsis_count": int,
            "compression_ratio": float,  # synopsis chars / input chars
        }
    """
    if not promoted:
        return {
            "synopsis_lines": [],
            "clusters": [],
            "params": {"cluster_threshold": cluster_threshold, "max_lines": max_lines, "representative": representative},
            "input_count": 0,
            "synopsis_count": 0,
            "compression_ratio": 0.0,
        }

    # Build word sets for each claim
    items = []
    for c in promoted:
        text = c.get("text", "").strip()
        if text:
            items.append((text, _normalize_words(text), c))

    # Greedy clustering: assign each claim to first cluster with overlap > threshold
    clusters: list[list[tuple[str, set[str], dict[str, Any]]]] = []

    for text, words, claim in items:
        placed = False
        for cluster in clusters:
            # Compare against cluster centroid (union of all member words)
            centroid_words: set[str] = set()
            for _, cw, _ in cluster:
                centroid_words |= cw
            overlap = _word_overlap(words, centroid_words)
            if overlap >= cluster_threshold:
                cluster.append((text, words, claim))
                placed = True
                break
        if not placed:
            clusters.append([(text, words, claim)])

    # Sort clusters by size (largest first)
    clusters.sort(key=lambda c: len(c), reverse=True)

    # Pick representative from each cluster
    synopsis_clusters = []
    for cluster in clusters[:max_lines]:
        if representative == "longest":
            rep = max(cluster, key=lambda x: len(x[0]))
        else:
            rep = cluster[0]

        synopsis_clusters.append({
            "representative": rep[0],
            "claim_type": rep[2].get("claim_type", ""),
            "members": [t for t, _, _ in cluster],
            "size": len(cluster),
        })

    synopsis_lines = [sc["representative"] for sc in synopsis_clusters]

    # Compression ratio: synopsis chars / total promoted chars
    total_input_chars = sum(len(t) for t, _, _ in items)
    synopsis_chars = sum(len(line) for line in synopsis_lines)
    ratio = synopsis_chars / total_input_chars if total_input_chars > 0 else 0.0

    return {
        "synopsis_lines": synopsis_lines,
        "clusters": synopsis_clusters,
        "params": {
            "cluster_threshold": cluster_threshold,
            "max_lines": max_lines,
            "representative": representative,
        },
        "input_count": len(items),
        "synopsis_count": len(synopsis_lines),
        "compression_ratio": round(ratio, 4),
    }


# ---------------------------------------------------------------------------


def synthesize_with_embeddings(
    promoted: list[dict[str, Any]],
    embeddings: "np.ndarray",  # type: ignore[name-defined]
    cluster_threshold: float = 0.5,
    max_lines: int = 7,
    representative: str = "longest",
) -> dict[str, Any]:
    """Compress promoted claims using embedding-based cosine similarity. Pure function.

    Same interface as synthesize() but clusters using vector similarity instead
    of Jaccard word overlap. Still pure: same embeddings + same params = same output.

    Args:
        promoted: list of promoted claim dicts (from promote())
        embeddings: numpy array of shape (len(promoted), embedding_dim)
        cluster_threshold: cosine similarity threshold to group claims (0.0-1.0)
            Higher = more clustering (claims need to be more similar)
            Lower = less clustering
        max_lines: max number of representative claims in synopsis
        representative: how to pick the cluster rep ("longest" or "first")

    Returns:
        Same shape as synthesize() output.
    """
    import numpy as np

    if not promoted or len(embeddings) == 0:
        return {
            "synopsis_lines": [],
            "clusters": [],
            "params": {
                "cluster_threshold": cluster_threshold,
                "max_lines": max_lines,
                "representative": representative,
                "method": "embeddings",
            },
            "input_count": 0,
            "synopsis_count": 0,
            "compression_ratio": 0.0,
        }

    # Build items list, filtering empty texts
    items: list[tuple[str, int, dict[str, Any]]] = []
    for i, c in enumerate(promoted):
        text = c.get("text", "").strip()
        if text and i < len(embeddings):
            items.append((text, i, c))

    if not items:
        return {
            "synopsis_lines": [],
            "clusters": [],
            "params": {
                "cluster_threshold": cluster_threshold,
                "max_lines": max_lines,
                "representative": representative,
                "method": "embeddings",
            },
            "input_count": 0,
            "synopsis_count": 0,
            "compression_ratio": 0.0,
        }

    # Compute pairwise cosine similarity
    # Normalize vectors for cosine sim via dot product
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)  # avoid division by zero
    normed = embeddings / norms

    # Greedy clustering: assign each claim to first cluster with similarity > threshold
    clusters: list[list[tuple[str, int, dict[str, Any]]]] = []
    cluster_centroids: list[np.ndarray] = []

    for text, idx, claim in items:
        vec = normed[idx]
        placed = False

        for ci, centroid in enumerate(cluster_centroids):
            similarity = float(np.dot(vec, centroid))
            if similarity >= cluster_threshold:
                clusters[ci].append((text, idx, claim))
                # Update centroid as mean of cluster member vectors
                member_vecs = np.array([normed[midx] for _, midx, _ in clusters[ci]])
                new_centroid = member_vecs.mean(axis=0)
                norm = np.linalg.norm(new_centroid)
                if norm > 0:
                    new_centroid = new_centroid / norm
                cluster_centroids[ci] = new_centroid
                placed = True
                break

        if not placed:
            clusters.append([(text, idx, claim)])
            cluster_centroids.append(vec.copy())

    # Sort clusters by size (largest first)
    clusters.sort(key=lambda c: len(c), reverse=True)

    # Pick representative from each cluster
    synopsis_clusters = []
    for cluster in clusters[:max_lines]:
        if representative == "longest":
            rep = max(cluster, key=lambda x: len(x[0]))
        else:
            rep = cluster[0]

        synopsis_clusters.append({
            "representative": rep[0],
            "claim_type": rep[2].get("claim_type", ""),
            "members": [t for t, _, _ in cluster],
            "size": len(cluster),
        })

    synopsis_lines = [sc["representative"] for sc in synopsis_clusters]

    # Compression ratio
    total_input_chars = sum(len(t) for t, _, _ in items)
    synopsis_chars = sum(len(line) for line in synopsis_lines)
    ratio = synopsis_chars / total_input_chars if total_input_chars > 0 else 0.0

    return {
        "synopsis_lines": synopsis_lines,
        "clusters": synopsis_clusters,
        "params": {
            "cluster_threshold": cluster_threshold,
            "max_lines": max_lines,
            "representative": representative,
            "method": "embeddings",
        },
        "input_count": len(items),
        "synopsis_count": len(synopsis_lines),
        "compression_ratio": round(ratio, 4),
    }


# ---------------------------------------------------------------------------


def check_sieve(card: dict[str, Any], input_stats: dict[str, Any]) -> dict[str, Any]:
    """Run structural checks on a sieve card. Pure function.

    Args:
        card: the structured narrative output (from structure())
        input_stats: dict with at minimum 'input_count'

    Returns:
        Dict of check name -> bool results.
    """
    promoted_count = card.get("promoted_count", 0)
    deferred_count = card.get("deferred_count", 0)
    dropped_count = card.get("dropped_count", 0)
    input_count = input_stats.get("input_count", 0)
    compression_ratio = card.get("compression_ratio", 1.0)
    declared_loss = card.get("declared_loss", [])

    # compression_holds: output chars < input chars
    compression_holds = compression_ratio < 1.0

    # no_empty_sections: at least one promoted claim exists
    no_empty_sections = promoted_count > 0

    # loss_declared: if anything was dropped, it must have loss entries
    # A clean topic with 0 drops is fine — that's not a failure
    loss_declared = (dropped_count == 0) or (len(declared_loss) > 0)

    # all_drops_have_reasons: every loss entry has a reason
    all_drops_have_reasons = all(
        bool(entry.get("reason")) for entry in declared_loss
    ) if declared_loss else True

    # deferred_bounded: deferred count < 50% of input
    deferred_bounded = (
        deferred_count < (input_count * 0.5)
    ) if input_count > 0 else True

    return {
        "compression_holds": compression_holds,
        "no_empty_sections": no_empty_sections,
        "loss_declared": loss_declared,
        "all_drops_have_reasons": all_drops_have_reasons,
        "deferred_bounded": deferred_bounded,
    }


def structure(
    promoted: list[dict[str, Any]],
    topic_context: dict[str, Any],
    loss_declarations: list[dict[str, Any]] | None = None,
    deferred: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structure promoted claims into a TopicNarrative. Pure function.

    Args:
        promoted: list of promoted claims from promote()
        topic_context: dict with 'handle', 'title', 'description', 'keywords'
        loss_declarations: loss list from promote() stage
        deferred: deferred claims list from promote() stage

    Returns:
        TopicNarrative dict with grouped claims, summary, and metrics.
    """
    loss_declarations = loss_declarations or []
    deferred = deferred or []

    # Group by (possibly reclassified) type
    facts: list[dict[str, Any]] = []
    principles: list[dict[str, Any]] = []
    hypotheses: list[dict[str, Any]] = []
    open_questions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []

    for c in promoted:
        ct = c.get("claim_type", "").lower()
        if ct == "fact":
            facts.append(c)
        elif ct in ("principle", "design_decision", "design decision"):
            principles.append(c)
        elif ct == "hypothesis":
            hypotheses.append(c)
        elif ct == "question":
            open_questions.append(c)
        elif ct == "observation":
            observations.append(c)
        else:
            other.append(c)

    # Build summary from strongest claims across types (not just facts)
    summary_pool = (
        [(c, "fact") for c in facts] +
        [(c, "principle") for c in principles] +
        [(c, "observation") for c in observations]
    )
    sorted_pool = sorted(
        summary_pool,
        key=lambda pair: pair[0].get("confidence", 0) or 0,
        reverse=True,
    )
    top_claims = sorted_pool[:3] if sorted_pool else []
    summary_sentences = [c.get("text", "").rstrip(".") + "." for c, _ in top_claims]
    summary = " ".join(summary_sentences) if summary_sentences else topic_context.get("title", "")

    # Compute input/output sizes for compression ratio
    # Input = all claims that entered the sieve (promoted + deferred + dropped)
    input_chars = (
        sum(len(c.get("text", "")) for c in promoted)
        + sum(len(c.get("text", "")) for c in deferred)
        + sum(len(l.get("claim_text", "")) for l in loss_declarations)
    )
    output_groups = facts + principles + hypotheses + open_questions + observations + other
    # Exclude summary from output_chars since it's derived from facts already counted
    output_chars = sum(len(c.get("text", "")) for c in output_groups)

    compression_ratio = output_chars / input_chars if input_chars > 0 else 0.0

    # Compression is a CHECK, not enforcement — report don't truncate
    compression_holds = compression_ratio < 1.0

    return {
        "title": topic_context.get("title", ""),
        "summary": summary,
        "facts": facts,
        "principles": principles,
        "hypotheses": hypotheses,
        "open_questions": open_questions,
        "observations": observations,
        "deferred": deferred,
        "declared_loss": loss_declarations,
        "compression_ratio": round(compression_ratio, 4),
        "compression_holds": compression_holds,
        "promoted_count": len(promoted),
        "deferred_count": len(deferred),
        "dropped_count": len(loss_declarations),
    }


# ---------------------------------------------------------------------------
# Stage 3: sieve_topic — orchestrator (uses store)
# ---------------------------------------------------------------------------

