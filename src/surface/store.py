"""Substrate store — SQLite-backed durable layer.

One file. Append-only tx log. Content-addressed blobs. Immutable stamps/facts/edges.
Mutable projections (derived, non-authoritative).

Design rules:
- blobs, stamps, facts, edges, tx_log: NEVER updated or deleted
- projections: mutable, rebuildable from immutable sources
- tx_log: hash-chained from genesis, total order over all writes
- blobs: content-addressed, idempotent writes (INSERT OR IGNORE)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .stamp import Stamp, h, _canonical_json


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "substrate.store.v1"

_DDL = """
-- Content-addressed immutable blob storage
CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY,
    data BLOB NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- Append-only hash-chained transaction log
CREATE TABLE IF NOT EXISTS tx_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);

-- Registered deterministic transforms
CREATE TABLE IF NOT EXISTS transforms (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    language TEXT NOT NULL,
    registered_at TEXT NOT NULL
);

-- Execution receipts (the core provenance layer)
CREATE TABLE IF NOT EXISTS stamps (
    stamp_hash TEXT PRIMARY KEY,
    schema TEXT NOT NULL,
    domain TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    fn_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    prev_stamp_hash TEXT NOT NULL,
    input_blob_ref TEXT,
    output_blob_ref TEXT,
    created_at TEXT NOT NULL
);

-- Immutable fact rows extracted from transform outputs
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    stamp_hash TEXT NOT NULL REFERENCES stamps(stamp_hash),
    domain TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Directed relationships between facts
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    stamp_hash TEXT NOT NULL REFERENCES stamps(stamp_hash),
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Materialized projections (the ONLY mutable table)
CREATE TABLE IF NOT EXISTS projections (
    key TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    data TEXT NOT NULL,
    derived_from_seq INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

-- Indexes for common access patterns
CREATE INDEX IF NOT EXISTS idx_stamps_domain ON stamps(domain);
CREATE INDEX IF NOT EXISTS idx_stamps_prev ON stamps(prev_stamp_hash);
CREATE INDEX IF NOT EXISTS idx_facts_stamp ON facts(stamp_hash);
CREATE INDEX IF NOT EXISTS idx_facts_domain_type ON facts(domain, type);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_tx_log_subject ON tx_log(subject_type, subject_id);
"""


# ---------------------------------------------------------------------------
# Store initialization
# ---------------------------------------------------------------------------

def init_db(path: str) -> sqlite3.Connection:
    """Create or open a substrate store. Returns a connection.

    Creates all tables if they don't exist. Enforces WAL mode for
    concurrent reads. Seeds the tx_log with a genesis entry if empty.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)

    # Seed genesis if tx_log is empty
    row = conn.execute("SELECT COUNT(*) FROM tx_log").fetchone()
    if row[0] == 0:
        genesis_hash = h(b"genesis")
        _tx_append_raw(conn, "store.init", "store", SCHEMA_VERSION, genesis_hash)
        conn.commit()

    return conn


# ---------------------------------------------------------------------------
# Blob writes
# ---------------------------------------------------------------------------

def blob_write(conn: sqlite3.Connection, data: bytes) -> str:
    """Write a content-addressed blob. Idempotent. Returns the hash."""
    blob_hash = h(data)
    conn.execute(
        "INSERT OR IGNORE INTO blobs (hash, data, size, created_at) VALUES (?, ?, ?, ?)",
        (blob_hash, data, len(data), _now()),
    )
    return blob_hash


def blob_read(conn: sqlite3.Connection, blob_hash: str) -> bytes | None:
    """Read a blob by hash. Returns None if not found."""
    row = conn.execute("SELECT data FROM blobs WHERE hash = ?", (blob_hash,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Stamp writes
# ---------------------------------------------------------------------------

def stamp_write(
    conn: sqlite3.Connection,
    stmp: Stamp,
    input_blob_ref: str | None = None,
    output_blob_ref: str | None = None,
) -> None:
    """Write an execution receipt. Idempotent (content-addressed by stamp_hash)."""
    conn.execute(
        """INSERT OR IGNORE INTO stamps
           (stamp_hash, schema, domain, input_hash, fn_hash, output_hash,
            prev_stamp_hash, input_blob_ref, output_blob_ref, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            stmp.stamp_hash, stmp.schema, stmp.domain,
            stmp.input_hash, stmp.fn_hash, stmp.output_hash,
            stmp.prev_stamp_hash, input_blob_ref, output_blob_ref, _now(),
        ),
    )


def stamp_read(conn: sqlite3.Connection, stamp_hash: str) -> dict | None:
    """Read a stamp by hash. Returns dict or None."""
    row = conn.execute(
        "SELECT stamp_hash, schema, domain, input_hash, fn_hash, output_hash, "
        "prev_stamp_hash, input_blob_ref, output_blob_ref FROM stamps WHERE stamp_hash = ?",
        (stamp_hash,),
    ).fetchone()
    if not row:
        return None
    return {
        "stamp_hash": row[0], "schema": row[1], "domain": row[2],
        "input_hash": row[3], "fn_hash": row[4], "output_hash": row[5],
        "prev_stamp_hash": row[6], "input_blob_ref": row[7], "output_blob_ref": row[8],
    }


def stamp_chain(conn: sqlite3.Connection, tip_hash: str, limit: int = 100) -> list[dict]:
    """Walk a stamp chain backward from tip. Returns stamps in chain order (oldest first)."""
    chain = []
    current = tip_hash
    genesis = h(b"genesis")
    while current and current != genesis and len(chain) < limit:
        stmp = stamp_read(conn, current)
        if not stmp:
            break
        chain.append(stmp)
        current = stmp["prev_stamp_hash"]
    chain.reverse()
    return chain


# ---------------------------------------------------------------------------
# Fact writes
# ---------------------------------------------------------------------------

def fact_write(
    conn: sqlite3.Connection,
    fact_id: str,
    stamp_hash: str,
    domain: str,
    fact_type: str,
    content: str,
) -> None:
    """Write an immutable fact row. Idempotent by fact_id."""
    conn.execute(
        "INSERT OR IGNORE INTO facts (id, stamp_hash, domain, type, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (fact_id, stamp_hash, domain, fact_type, content, _now()),
    )


def facts_by_stamp(conn: sqlite3.Connection, stamp_hash: str) -> list[dict]:
    """Read all facts produced by a stamp."""
    rows = conn.execute(
        "SELECT id, stamp_hash, domain, type, content FROM facts WHERE stamp_hash = ?",
        (stamp_hash,),
    ).fetchall()
    return [{"id": r[0], "stamp_hash": r[1], "domain": r[2], "type": r[3], "content": r[4]} for r in rows]


# ---------------------------------------------------------------------------
# Edge writes
# ---------------------------------------------------------------------------

def edge_write(
    conn: sqlite3.Connection,
    edge_id: str,
    stamp_hash: str,
    source_id: str,
    target_id: str,
    relation: str,
) -> None:
    """Write an immutable edge row. Idempotent by edge_id."""
    conn.execute(
        "INSERT OR IGNORE INTO edges (id, stamp_hash, source_id, target_id, relation, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (edge_id, stamp_hash, source_id, target_id, relation, _now()),
    )


# ---------------------------------------------------------------------------
# TX log
# ---------------------------------------------------------------------------

def _tx_append_raw(
    conn: sqlite3.Connection,
    action: str,
    subject_type: str,
    subject_id: str,
    prev_hash: str,
) -> str:
    """Append a raw tx_log entry. Returns entry_hash. Internal use."""
    ts = _now()
    entry = _canonical_json({
        "ts": ts,
        "action": action,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "prev_hash": prev_hash,
    })
    entry_hash = h(entry.encode())
    conn.execute(
        "INSERT INTO tx_log (ts, action, subject_type, subject_id, prev_hash, entry_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, action, subject_type, subject_id, prev_hash, entry_hash),
    )
    return entry_hash


def tx_append(
    conn: sqlite3.Connection,
    action: str,
    subject_type: str,
    subject_id: str,
) -> str:
    """Append to the tx_log, chaining from the last entry. Returns entry_hash."""
    row = conn.execute(
        "SELECT entry_hash FROM tx_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = row[0] if row else h(b"genesis")
    return _tx_append_raw(conn, action, subject_type, subject_id, prev_hash)


def tx_head(conn: sqlite3.Connection) -> dict | None:
    """Get the latest tx_log entry."""
    row = conn.execute(
        "SELECT seq, ts, action, subject_type, subject_id, prev_hash, entry_hash "
        "FROM tx_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "seq": row[0], "ts": row[1], "action": row[2],
        "subject_type": row[3], "subject_id": row[4],
        "prev_hash": row[5], "entry_hash": row[6],
    }


def tx_verify(conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    """Verify the tx_log hash chain from genesis. Returns (ok, errors)."""
    rows = conn.execute(
        "SELECT seq, ts, action, subject_type, subject_id, prev_hash, entry_hash "
        "FROM tx_log ORDER BY seq ASC"
    ).fetchall()

    errors = []
    prev_hash = h(b"genesis")

    for row in rows:
        seq, ts, action, subject_type, subject_id, claimed_prev, claimed_hash = row

        if claimed_prev != prev_hash:
            errors.append(f"seq {seq}: prev_hash mismatch (expected {prev_hash[:16]}, got {claimed_prev[:16]})")

        entry = _canonical_json({
            "ts": ts,
            "action": action,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "prev_hash": claimed_prev,
        })
        computed = h(entry.encode())
        if computed != claimed_hash:
            errors.append(f"seq {seq}: entry_hash mismatch")

        prev_hash = claimed_hash

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Projection writes
# ---------------------------------------------------------------------------

def projection_write(
    conn: sqlite3.Connection,
    key: str,
    domain: str,
    data: dict,
) -> None:
    """Write or update a materialized projection. This is the ONLY mutable write."""
    head = tx_head(conn)
    seq = head["seq"] if head else 0
    conn.execute(
        "INSERT OR REPLACE INTO projections (key, domain, data, derived_from_seq, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (key, domain, json.dumps(data, sort_keys=True), seq, _now()),
    )


def projection_read(conn: sqlite3.Connection, key: str) -> dict | None:
    """Read a projection by key."""
    row = conn.execute(
        "SELECT data FROM projections WHERE key = ?", (key,)
    ).fetchone()
    return json.loads(row[0]) if row else None


# ---------------------------------------------------------------------------
# Composite write path
# ---------------------------------------------------------------------------

def inscribe_receipt(
    conn: sqlite3.Connection,
    stmp: Stamp,
    input_data: bytes,
    output_data: bytes,
    facts: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> str:
    """Full canonical write path: blob + stamp + facts + edges + tx_log.

    This is the single entry point for durable writes. Everything goes
    through here to ensure the tx_log captures every mutation.

    Returns the stamp_hash.
    """
    # 1. Write input and output blobs
    input_ref = blob_write(conn, input_data)
    output_ref = blob_write(conn, output_data)

    # 2. Write the stamp
    stamp_write(conn, stmp, input_blob_ref=input_ref, output_blob_ref=output_ref)

    # 3. Write facts
    if facts:
        for f in facts:
            fact_write(conn, f["id"], stmp.stamp_hash, f["domain"], f["type"], f["content"])

    # 4. Write edges
    if edges:
        for e in edges:
            edge_write(conn, e["id"], stmp.stamp_hash, e["source_id"], e["target_id"], e["relation"])

    # 5. Append to tx_log
    tx_append(conn, "receipt.inscribed", "stamp", stmp.stamp_hash)

    conn.commit()
    return stmp.stamp_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    """ISO timestamp. The only impure function in this module."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
