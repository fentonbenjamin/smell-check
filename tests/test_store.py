"""Store layer tests — the durable foundation.

Tests the SQLite store in isolation: blob writes, stamp writes,
fact writes, tx_log chain integrity, projection writes, and the
composite inscribe_receipt path.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

# sys.path handled by package layout

from smell_check.stamp import stamp, verify_stamp, h, GENESIS, _canonical_json
from smell_check.store import (
    init_db,
    blob_write, blob_read,
    stamp_write, stamp_read, stamp_chain,
    fact_write, facts_by_stamp,
    edge_write,
    tx_append, tx_head, tx_verify,
    projection_write, projection_read,
    inscribe_receipt,
)


@pytest.fixture
def db():
    """Fresh in-memory database for each test."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Schema and init
# ---------------------------------------------------------------------------

class TestInit:
    def test_creates_tables(self, db):
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "blobs" in tables
        assert "tx_log" in tables
        assert "stamps" in tables
        assert "facts" in tables
        assert "edges" in tables
        assert "projections" in tables
        assert "transforms" in tables

    def test_seeds_genesis_tx(self, db):
        head = tx_head(db)
        assert head is not None
        assert head["action"] == "store.init"
        assert head["seq"] == 1

    def test_tx_log_genesis_is_valid(self, db):
        ok, errors = tx_verify(db)
        assert ok, f"Genesis tx chain invalid: {errors}"

    def test_idempotent_init(self):
        """Opening the same db twice doesn't duplicate genesis."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            conn1 = init_db(path)
            conn1.close()
            conn2 = init_db(path)
            count = conn2.execute("SELECT COUNT(*) FROM tx_log").fetchone()[0]
            conn2.close()
            assert count == 1  # only one genesis
        finally:
            Path(path).unlink()


# ---------------------------------------------------------------------------
# Blobs
# ---------------------------------------------------------------------------

class TestBlobs:
    def test_write_and_read(self, db):
        data = b"hello world"
        blob_hash = blob_write(db, data)
        assert len(blob_hash) == 64  # SHA-256 hex
        result = blob_read(db, blob_hash)
        assert result == data

    def test_content_addressed(self, db):
        """Same content → same hash, no duplicate rows."""
        h1 = blob_write(db, b"same")
        h2 = blob_write(db, b"same")
        assert h1 == h2
        count = db.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]
        assert count == 1

    def test_different_content_different_hash(self, db):
        h1 = blob_write(db, b"alpha")
        h2 = blob_write(db, b"beta")
        assert h1 != h2

    def test_read_missing(self, db):
        assert blob_read(db, "nonexistent") is None


# ---------------------------------------------------------------------------
# Stamps
# ---------------------------------------------------------------------------

class TestStamps:
    def test_write_and_read(self, db):
        s = stamp("test", "aaa", "bbb", "ccc", GENESIS)
        stamp_write(db, s)
        db.commit()
        result = stamp_read(db, s.stamp_hash)
        assert result is not None
        assert result["domain"] == "test"
        assert result["input_hash"] == "aaa"
        assert result["fn_hash"] == "bbb"
        assert result["output_hash"] == "ccc"

    def test_idempotent(self, db):
        s = stamp("test", "aaa", "bbb", "ccc", GENESIS)
        stamp_write(db, s)
        stamp_write(db, s)  # no error
        db.commit()
        count = db.execute("SELECT COUNT(*) FROM stamps").fetchone()[0]
        assert count == 1

    def test_read_missing(self, db):
        assert stamp_read(db, "nonexistent") is None

    def test_chain_traversal(self, db):
        s1 = stamp("test", "in1", "fn", "out1", GENESIS)
        s2 = stamp("test", "in2", "fn", "out2", s1.stamp_hash)
        s3 = stamp("test", "in3", "fn", "out3", s2.stamp_hash)
        stamp_write(db, s1)
        stamp_write(db, s2)
        stamp_write(db, s3)
        db.commit()

        chain = stamp_chain(db, s3.stamp_hash)
        assert len(chain) == 3
        assert chain[0]["stamp_hash"] == s1.stamp_hash
        assert chain[1]["stamp_hash"] == s2.stamp_hash
        assert chain[2]["stamp_hash"] == s3.stamp_hash


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

class TestFacts:
    def test_write_and_read(self, db):
        s = stamp("test", "a", "b", "c", GENESIS)
        stamp_write(db, s)
        fact_write(db, "fact_001", s.stamp_hash, "test", "verdict", '{"result": "WITNESSED"}')
        db.commit()

        facts = facts_by_stamp(db, s.stamp_hash)
        assert len(facts) == 1
        assert facts[0]["id"] == "fact_001"
        assert facts[0]["type"] == "verdict"

    def test_multiple_facts_per_stamp(self, db):
        s = stamp("test", "a", "b", "c", GENESIS)
        stamp_write(db, s)
        fact_write(db, "f1", s.stamp_hash, "test", "claim", "claim content")
        fact_write(db, "f2", s.stamp_hash, "test", "verdict", "verdict content")
        db.commit()

        facts = facts_by_stamp(db, s.stamp_hash)
        assert len(facts) == 2


# ---------------------------------------------------------------------------
# TX log
# ---------------------------------------------------------------------------

class TestTxLog:
    def test_chain_integrity(self, db):
        tx_append(db, "test.action1", "test", "id1")
        tx_append(db, "test.action2", "test", "id2")
        tx_append(db, "test.action3", "test", "id3")
        db.commit()

        ok, errors = tx_verify(db)
        assert ok, f"Chain broken: {errors}"

    def test_head_advances(self, db):
        h1 = tx_head(db)
        tx_append(db, "test.action", "test", "id1")
        db.commit()
        h2 = tx_head(db)
        assert h2["seq"] > h1["seq"]
        assert h2["action"] == "test.action"

    def test_tampered_entry_detected(self, db):
        tx_append(db, "real.action", "test", "id1")
        db.commit()

        # Tamper with the last entry
        db.execute(
            "UPDATE tx_log SET action = 'tampered' WHERE seq = (SELECT MAX(seq) FROM tx_log)"
        )
        db.commit()

        ok, errors = tx_verify(db)
        assert not ok, "Tampered tx_log should fail verification"


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------

class TestProjections:
    def test_write_and_read(self, db):
        projection_write(db, "summary:test", "test", {"count": 5})
        db.commit()
        result = projection_read(db, "summary:test")
        assert result == {"count": 5}

    def test_mutable_update(self, db):
        projection_write(db, "summary:test", "test", {"count": 1})
        projection_write(db, "summary:test", "test", {"count": 2})
        db.commit()
        result = projection_read(db, "summary:test")
        assert result["count"] == 2

    def test_read_missing(self, db):
        assert projection_read(db, "nonexistent") is None


# ---------------------------------------------------------------------------
# Composite write path
# ---------------------------------------------------------------------------

class TestInscribeReceipt:
    def test_full_write_path(self, db):
        """The canonical write path: blob + stamp + facts + tx_log."""
        input_data = b'{"claims": [{"action": "start", "subject": "server"}]}'
        output_data = b'{"summary": {"witnessed": 1}, "verdicts": [...]}'

        input_hash = h(input_data)
        output_hash = h(output_data)
        fn_hash = h(b"test_function_source")

        s = stamp("witness_verdict", input_hash, fn_hash, output_hash, GENESIS)

        result_hash = inscribe_receipt(
            db, s,
            input_data=input_data,
            output_data=output_data,
            facts=[
                {"id": "f1", "domain": "witness_verdict", "type": "verdict",
                 "content": '{"verdict": "WITNESSED"}'},
            ],
        )

        assert result_hash == s.stamp_hash

        # Verify all artifacts were written
        assert blob_read(db, input_hash) == input_data
        assert blob_read(db, output_hash) == output_data
        assert stamp_read(db, s.stamp_hash) is not None
        assert len(facts_by_stamp(db, s.stamp_hash)) == 1

        # Verify tx_log was appended
        head = tx_head(db)
        assert head["action"] == "receipt.inscribed"
        assert head["subject_id"] == s.stamp_hash

        # Verify chain integrity
        ok, errors = tx_verify(db)
        assert ok, f"TX chain broken after inscribe: {errors}"

    def test_stamp_binds_blobs(self, db):
        """The stamp's input/output hashes must match the stored blobs."""
        input_data = b"test input"
        output_data = b"test output"

        s = stamp("test", h(input_data), h(b"fn"), h(output_data), GENESIS)
        inscribe_receipt(db, s, input_data, output_data)

        stored = stamp_read(db, s.stamp_hash)
        assert stored["input_blob_ref"] == h(input_data)
        assert stored["output_blob_ref"] == h(output_data)

        # Verify binding: recompute hash from stored blob, compare to stamp
        stored_input = blob_read(db, stored["input_blob_ref"])
        assert h(stored_input) == stored["input_hash"]

    def test_receipt_chain_in_store(self, db):
        """Multiple receipts form a verifiable chain in the store."""
        fn_hash = h(b"fn")

        s1 = stamp("test", h(b"in1"), fn_hash, h(b"out1"), GENESIS)
        inscribe_receipt(db, s1, b"in1", b"out1")

        s2 = stamp("test", h(b"in2"), fn_hash, h(b"out2"), s1.stamp_hash)
        inscribe_receipt(db, s2, b"in2", b"out2")

        s3 = stamp("test", h(b"in3"), fn_hash, h(b"out3"), s2.stamp_hash)
        inscribe_receipt(db, s3, b"in3", b"out3")

        # Walk chain from tip
        chain = stamp_chain(db, s3.stamp_hash)
        assert len(chain) == 3
        assert chain[0]["stamp_hash"] == s1.stamp_hash
        assert chain[2]["stamp_hash"] == s3.stamp_hash

        # Full tx_log integrity
        ok, errors = tx_verify(db)
        assert ok, f"Chain broken: {errors}"
