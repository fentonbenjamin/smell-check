"""Chamber tests — proving the chip boundary.

Tests the blob lifecycle: staged → on-chip → off-chip → verifiable.
Tests boundary attestation separately from transform receipts.
Tests custody verification end-to-end.
"""

import sys
from pathlib import Path

import pytest

# sys.path handled by package layout

from smell_check.chamber import (
    stage_blob,
    measure_chamber,
    attest_boundary,
    process_through_chamber,
    verify_custody,
)
from smell_check.stamp import h
from smell_check.projections import project_consumer, project_smell_check


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------

class TestStagedBlob:

    def test_text_staged_immutably(self):
        staged = stage_blob("hello world")
        assert staged.blob_hash == h(b"hello world")
        assert staged.byte_length == 11
        assert staged.raw_bytes == b"hello world"

    def test_bytes_staged_immutably(self):
        staged = stage_blob(b"\xff\xfe binary data")
        assert staged.byte_length == len(b"\xff\xfe binary data")

    def test_same_content_same_hash(self):
        s1 = stage_blob("same text")
        s2 = stage_blob("same text")
        assert s1.blob_hash == s2.blob_hash

    def test_different_content_different_hash(self):
        s1 = stage_blob("alpha")
        s2 = stage_blob("beta")
        assert s1.blob_hash != s2.blob_hash

    def test_capture_metadata(self):
        staged = stage_blob(
            "thread content",
            capture_source="share",
            capture_agent="iphone-14",
            source_metadata={"thread_id": "abc123"},
        )
        assert staged.capture_source == "share"
        assert staged.capture_agent == "iphone-14"
        assert staged.source_metadata["thread_id"] == "abc123"
        assert staged.capture_ts  # timestamp exists

    def test_staged_blob_is_frozen(self):
        staged = stage_blob("immutable")
        with pytest.raises(AttributeError):
            staged.blob_hash = "tampered"


# ---------------------------------------------------------------------------
# Chamber measurement
# ---------------------------------------------------------------------------

class TestChamberMeasurement:

    def test_measurement_has_all_hashes(self):
        m = measure_chamber()
        assert "chamber_hash" in m
        assert "kernel_hash" in m
        assert "perceiver_hash" in m
        assert "sieve_hash" in m
        assert "types_hash" in m
        assert "stamp_hash" in m

    def test_measurement_is_deterministic(self):
        m1 = measure_chamber()
        m2 = measure_chamber()
        assert m1["chamber_hash"] == m2["chamber_hash"]
        assert m1["kernel_hash"] == m2["kernel_hash"]

    def test_all_hashes_are_valid_sha256(self):
        m = measure_chamber()
        for key, val in m.items():
            assert len(val) == 64, f"{key} is not a valid SHA-256 hex"


# ---------------------------------------------------------------------------
# Boundary attestation
# ---------------------------------------------------------------------------

class TestBoundaryAttestation:

    def test_attestation_binds_blob_to_chamber(self):
        staged = stage_blob("test input")
        chamber = measure_chamber()
        att = attest_boundary(staged, chamber)

        assert att.blob_hash == staged.blob_hash
        assert att.chamber_hash == chamber["chamber_hash"]
        assert att.security_mode == "soft"
        assert len(att.attestation_hash) == 64

    def test_different_blob_different_attestation(self):
        chamber = measure_chamber()
        att1 = attest_boundary(stage_blob("input A"), chamber)
        att2 = attest_boundary(stage_blob("input B"), chamber)
        assert att1.attestation_hash != att2.attestation_hash

    def test_attestation_self_hash_verifiable(self):
        staged = stage_blob("verify me")
        chamber = measure_chamber()
        att = attest_boundary(staged, chamber)

        # Recompute the attestation hash from its fields
        # perception_mode and execution_class are proof-bearing
        from smell_check.stamp import _canonical_json
        partial = {
            "blob_hash": att.blob_hash,
            "chamber_hash": att.chamber_hash,
            "perceiver_hash": att.perceiver_hash,
            "kernel_hash": att.kernel_hash,
            "config_hash": att.config_hash,
            "perception_mode": att.perception_mode,
            "execution_class": att.execution_class,
            "security_mode": att.security_mode,
            "ingress_ts": att.ingress_ts,
        }
        recomputed = h(_canonical_json(partial).encode())
        assert recomputed == att.attestation_hash

    def test_security_mode_propagates(self):
        staged = stage_blob("test")
        chamber = measure_chamber()
        att = attest_boundary(staged, chamber, security_mode="measured")
        assert att.security_mode == "measured"


# ---------------------------------------------------------------------------
# Full lifecycle: process_through_chamber
# ---------------------------------------------------------------------------

class TestProcessThroughChamber:

    def test_full_lifecycle_returns_all_stages(self):
        result = process_through_chamber("The server starts on port 8080")

        assert "staged_blob" in result
        assert "boundary_attestation" in result
        assert "authoritative_output" in result
        assert "chamber_measurement" in result
        assert "security_mode" in result

    def test_staged_blob_hash_is_correct(self):
        text = "Test input for staging"
        result = process_through_chamber(text)
        expected_hash = h(text.encode("utf-8"))
        assert result["staged_blob"]["blob_hash"] == expected_hash

    def test_boundary_attestation_references_blob(self):
        result = process_through_chamber("Attestation test")
        assert result["boundary_attestation"]["blob_hash"] == result["staged_blob"]["blob_hash"]

    def test_authoritative_output_has_governed_state(self):
        result = process_through_chamber("The API guarantees backwards compatibility")
        auth = result["authoritative_output"]
        gs = auth["governed_state"]
        assert "promoted" in gs
        assert "contested" in gs
        assert "deferred" in gs
        assert "output_hash" in auth

    def test_authoritative_output_has_receipt_chain(self):
        result = process_through_chamber("Test with receipts")
        chain = result["authoritative_output"]["receipt_chain"]
        assert "stamps" in chain
        assert "chain_length" in chain

    def test_attestation_hash_links_output_to_boundary(self):
        result = process_through_chamber("Linking test")
        att_hash = result["boundary_attestation"]["attestation_hash"]
        output_att = result["authoritative_output"]["attestation_hash"]
        assert att_hash == output_att

    def test_deterministic_same_input(self):
        r1 = process_through_chamber("Determinism check")
        r2 = process_through_chamber("Determinism check")
        # Blob hash must match
        assert r1["staged_blob"]["blob_hash"] == r2["staged_blob"]["blob_hash"]
        # Chamber hash must match
        assert r1["chamber_measurement"]["chamber_hash"] == r2["chamber_measurement"]["chamber_hash"]
        # Output hash must match
        assert r1["authoritative_output"]["output_hash"] == r2["authoritative_output"]["output_hash"]


# ---------------------------------------------------------------------------
# Custody verification
# ---------------------------------------------------------------------------

class TestCustodyVerification:

    def test_valid_custody_record_verifies(self):
        record = process_through_chamber("Verify this custody chain")
        result = verify_custody(record)
        assert result["valid"], f"Errors: {result['errors']}"
        assert result["security_mode"] == "soft"

    def test_tampered_governed_state_fails(self):
        record = process_through_chamber(
            "The server guarantees sub-10ms latency for all API endpoints in production"
        )
        # Tamper with the governed state — inject a fake claim
        record["authoritative_output"]["governed_state"]["promoted"].append(
            {"text": "INJECTED CLAIM", "mother_type": "CONTRACT"}
        )
        result = verify_custody(record)
        assert not result["valid"]
        assert any("output hash" in e for e in result["errors"])

    def test_tampered_attestation_fails(self):
        record = process_through_chamber("Attestation tamper test")
        # Tamper with the attestation
        record["boundary_attestation"]["blob_hash"] = "deadbeef" * 8
        result = verify_custody(record)
        assert not result["valid"]
        assert any("attestation" in e.lower() for e in result["errors"])

    def test_tampered_chamber_hash_fails(self):
        record = process_through_chamber("Chamber tamper test")
        record["chamber_measurement"]["chamber_hash"] = "0" * 64
        result = verify_custody(record)
        assert not result["valid"]
        assert any("chamber" in e.lower() for e in result["errors"])


# ---------------------------------------------------------------------------
# Projections are outside the boundary
# ---------------------------------------------------------------------------

class TestProjectionBoundary:

    def test_projections_derive_from_authoritative_output(self):
        """Projections consume the authoritative governed state.
        They are NOT part of the custody record."""
        record = process_through_chamber(
            "We decided on Friday pickup but need to confirm the restaurant"
        )
        # Authoritative output is inside the boundary
        gs = record["authoritative_output"]["governed_state"]

        # Projections are OUTSIDE the boundary — derived, not authoritative
        consumer = project_consumer(gs)
        pro = project_smell_check(gs)

        # Projections exist but are not in the custody record
        assert "cards" not in record  # no cards inside the boundary
        assert "consumer" not in record
        assert "pro" not in record

        # Projections are derived from the governed state
        assert isinstance(consumer["decided"], list)
        assert "findings" in pro
        assert "stable_points" in pro
        assert "open_questions" in pro


# ---------------------------------------------------------------------------
# Chain splicing detection (P0 fix)
# ---------------------------------------------------------------------------

class TestChainSplicingDetection:
    """A spliced record combines valid pieces from different runs.
    Verification must detect this."""

    def test_swapped_receipt_chain_detected(self):
        """Take attestation from run A, receipt chain from run B. Must fail."""
        record_a = process_through_chamber("Input A for splicing test with enough detail to promote")
        record_b = process_through_chamber("Input B completely different text about other topics")

        # Splice: keep A's attestation and blob, swap in B's receipt chain
        spliced = dict(record_a)
        spliced["authoritative_output"] = dict(record_a["authoritative_output"])
        spliced["authoritative_output"]["receipt_chain"] = record_b["authoritative_output"]["receipt_chain"]

        result = verify_custody(spliced)
        assert not result["valid"], "Spliced record should fail verification"

    def test_chain_link_broken_detected(self):
        """Break prev_stamp_hash linkage within a valid chain. Must fail."""
        record = process_through_chamber("Chain linkage test for verification")
        stamps = record["authoritative_output"]["receipt_chain"]["stamps"]

        if len(stamps) >= 2:
            # Break the chain by changing a prev_stamp_hash
            stamps[1] = dict(stamps[1])
            stamps[1]["prev_stamp_hash"] = "0" * 64

            result = verify_custody(record)
            assert not result["valid"]
            assert any("chain broken" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Durable inscription (P1 fix)
# ---------------------------------------------------------------------------

class TestCustodyInscription:
    """process_through_chamber with db_path must succeed and store correctly."""

    def test_inscription_does_not_crash(self):
        """Basic smoke test: db_path should not raise IntegrityError."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            record = process_through_chamber(
                "Inscription test for the chamber lifecycle",
                db_path=db_path,
            )
            # Should succeed without FK constraint errors
            assert record["authoritative_output"]["inscription_tx"]
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_nine_checks_all_pass(self):
        """All 9 verification checks must pass for a clean record."""
        record = process_through_chamber(
            "The API guarantees backwards compatibility for all v2 endpoints in production"
        )
        result = verify_custody(record)
        assert result["valid"], f"Errors: {result['errors']}"
        assert result["checks_performed"] == 9

    def test_governance_stamp_binds_output(self):
        """The final governance stamp's output_hash must match the authoritative output_hash."""
        record = process_through_chamber(
            "The server starts on port 8080 and guarantees sub-10ms response times"
        )
        stamps = record["authoritative_output"]["receipt_chain"]["stamps"]
        last = stamps[-1]
        assert last["domain"] == "governance"
        assert last["output_hash"] == record["authoritative_output"]["output_hash"]

    def test_fn_hash_matches_chamber_measurement(self):
        """Each pipeline stamp's fn_hash must match its chamber measurement hash."""
        record = process_through_chamber(
            "The database migration requires careful testing before production deploy"
        )
        stamps = record["authoritative_output"]["receipt_chain"]["stamps"]
        chamber = record["chamber_measurement"]

        domain_map = {
            "tagger": "perceiver_hash",
            "mother_types": "types_hash",
            "sieve": "sieve_hash",
            "governance": "kernel_hash",
        }
        for s in stamps:
            domain = s["domain"]
            chamber_key = domain_map.get(domain)
            if chamber_key:
                assert s["fn_hash"] == chamber[chamber_key], (
                    f"{domain} fn_hash {s['fn_hash'][:16]}... "
                    f"!= chamber {chamber_key} {chamber[chamber_key][:16]}..."
                )

    def test_forged_fn_hash_detected(self):
        """Swapping a stamp's fn_hash to a different value must fail check 7."""
        record = process_through_chamber(
            "Testing fn_hash binding to chamber measurement hashes"
        )
        stamps = record["authoritative_output"]["receipt_chain"]["stamps"]
        # Forge the tagger's fn_hash
        stamps[0] = dict(stamps[0])
        stamps[0]["fn_hash"] = "f" * 64

        result = verify_custody(record)
        assert not result["valid"]
        assert any("fn_hash does not match chamber" in e for e in result["errors"])

    def test_output_binding_fails_when_governance_stamp_missing(self):
        """If the governance stamp is removed, the chain→output binding breaks."""
        record = process_through_chamber(
            "Testing that the governance stamp is required for output binding"
        )
        # Remove the governance stamp (last in chain)
        stamps = record["authoritative_output"]["receipt_chain"]["stamps"]
        if stamps and stamps[-1]["domain"] == "governance":
            stamps.pop()
        # The last stamp is now the sieve, whose output_hash != authoritative output_hash
        result = verify_custody(record)
        assert not result["valid"]
        assert any("chain→output binding broken" in e for e in result["errors"])

    def test_inscribed_blob_matches_input(self):
        """The stored input blob must be the original text, not governed_state."""
        import tempfile
        from smell_check.store import init_db, blob_read
        from smell_check.stamp import h as sha256

        text = "The original input text that should be stored as-is"
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            record = process_through_chamber(text, db_path=db_path)
            conn = init_db(db_path)

            # The input blob should be the raw text bytes
            expected_hash = sha256(text.encode("utf-8"))
            stored = blob_read(conn, expected_hash)
            assert stored is not None, "Input blob not found in store"
            assert stored == text.encode("utf-8"), "Stored blob does not match input text"

            conn.close()
        finally:
            Path(db_path).unlink(missing_ok=True)
