"""Pipeline determinism tests — same input must produce same stamps.

The stamp pipeline (tagger -> mother_types -> sieve) must be deterministic:
same text in, same stamp hashes out. Transport IDs (time-based, entropy-based)
must not leak into the cryptographic binding.

This is the operational test for: "Can we replay and verify?"
If the pipeline is nondeterministic, replaying the same input produces
a different stamp, and the custody chain can't be independently verified.
"""

import sys
from pathlib import Path

import pytest

# sys.path handled by package layout

from smell_check.receipted import (
    run_tagger_with_receipt,
    run_sieve_with_receipt,
    run_pipeline_with_receipts,
    _hash_claims,
    _canonical_claim,
)
from smell_check.mother_types import (
    tagger_to_claims,
    make_witness,
    make_typed_unit,
)


class TestCanonicalClaim:
    """_canonical_claim must strip transport fields, keep content."""

    def test_strips_id(self):
        claim = {"id": "tu_abc_123", "text": "hello", "mother_type": "CONTRACT"}
        canonical = _canonical_claim(claim)
        assert "tu_abc_123" not in canonical
        assert "hello" in canonical

    def test_canonicalizes_witness(self):
        """Witness content stays in the stamped boundary; only its transport ID is stripped."""
        claim = {
            "id": "tu_abc",
            "text": "test",
            "_witness": {"id": "wit_xyz", "witness_type": "raw_turn", "observed_by": "tagger"},
        }
        canonical = _canonical_claim(claim)
        # Transport ID stripped
        assert "wit_xyz" not in canonical
        # Witness semantics kept
        assert "raw_turn" in canonical
        assert "tagger" in canonical
        assert "_witness" in canonical

    def test_preserves_semantic_fields(self):
        claim = {
            "id": "tu_abc",
            "text": "pure function",
            "mother_type": "CONTRACT",
            "confidence": 0.85,
            "actor": "claude",
        }
        canonical = _canonical_claim(claim)
        assert "pure function" in canonical
        assert "CONTRACT" in canonical
        assert "0.85" in canonical

    def test_same_content_different_ids_same_canonical(self):
        c1 = {"id": "tu_aaa_111", "text": "same", "mother_type": "CONTRACT"}
        c2 = {"id": "tu_bbb_222", "text": "same", "mother_type": "CONTRACT"}
        assert _canonical_claim(c1) == _canonical_claim(c2)


class TestHashClaimsDeterminism:
    """_hash_claims must produce identical hashes for semantically identical claims."""

    def test_same_claims_different_ids(self):
        """Two claim lists with different transport IDs must hash identically."""
        claims_a = [
            {"id": "tu_19d8e725_aaa", "text": "claim one", "mother_type": "CONTRACT",
             "_witness": {"id": "wit_19d8e725_bbb", "witness_type": "raw_turn"}},
            {"id": "tu_19d8e726_ccc", "text": "claim two", "mother_type": "CONSTRAINT",
             "_witness": {"id": "wit_19d8e726_ddd", "witness_type": "raw_turn"}},
        ]
        claims_b = [
            {"id": "tu_DIFFERENT_111", "text": "claim one", "mother_type": "CONTRACT",
             "_witness": {"id": "wit_DIFFERENT_222", "witness_type": "raw_turn"}},
            {"id": "tu_DIFFERENT_333", "text": "claim two", "mother_type": "CONSTRAINT",
             "_witness": {"id": "wit_DIFFERENT_444", "witness_type": "raw_turn"}},
        ]
        assert _hash_claims(claims_a) == _hash_claims(claims_b)

    def test_different_content_different_hash(self):
        claims_a = [{"id": "tu_x", "text": "alpha", "mother_type": "CONTRACT"}]
        claims_b = [{"id": "tu_x", "text": "beta", "mother_type": "CONTRACT"}]
        assert _hash_claims(claims_a) != _hash_claims(claims_b)


class TestTaggerDeterminism:
    """run_tagger_with_receipt must produce identical stamps for identical input."""

    def test_tagger_stamp_deterministic(self):
        text = "The API bridge starts a subprocess and listens on port 8080."
        r1 = run_tagger_with_receipt(text, turn_id="t1", actor="claude")
        r2 = run_tagger_with_receipt(text, turn_id="t1", actor="claude")
        assert r1["stamp"].stamp_hash == r2["stamp"].stamp_hash

    def test_tagger_different_input_different_stamp(self):
        r1 = run_tagger_with_receipt("alpha", turn_id="t1", actor="claude")
        r2 = run_tagger_with_receipt("beta", turn_id="t1", actor="claude")
        assert r1["stamp"].stamp_hash != r2["stamp"].stamp_hash


class TestMotherTypeBridgeDeterminism:
    """The mother type bridge must be deterministic through canonicalization."""

    def test_tagger_to_claims_same_content_same_hash(self):
        """Two runs of tagger_to_claims with same input must hash identically
        even though the generated IDs differ."""
        text = "This function guarantees O(1) lookup by content hash."
        tags = [{"event_type": "belief_formed", "confidence": 0.9, "span": text}]

        claims_a = tagger_to_claims(text, tags, actor="claude", turn_id="t1")
        claims_b = tagger_to_claims(text, tags, actor="claude", turn_id="t1")

        # IDs should be different (time-based)
        assert claims_a[0]["id"] != claims_b[0]["id"]

        # But hashes must be the same (canonicalized)
        assert _hash_claims(claims_a) == _hash_claims(claims_b)


class TestFullPipelineDeterminism:
    """The full pipeline must produce identical stamp chains."""

    def test_pipeline_stamps_deterministic(self):
        text = "The sieve promotes claims based on corroboration threshold."
        topic_context = {"topic": "sieve-design", "existing_claims": []}

        r1 = run_pipeline_with_receipts(
            text, topic_context, turn_id="t1", actor="claude"
        )
        r2 = run_pipeline_with_receipts(
            text, topic_context, turn_id="t1", actor="claude"
        )

        # Every stamp in the chain must match
        assert len(r1["stamps"]) == len(r2["stamps"])
        for s1, s2 in zip(r1["stamps"], r2["stamps"]):
            assert s1.stamp_hash == s2.stamp_hash, (
                f"Nondeterministic stamp in domain={s1.domain}: "
                f"{s1.stamp_hash[:16]} != {s2.stamp_hash[:16]}"
            )
