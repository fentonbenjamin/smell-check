"""Chamber — the bounded lawful transform environment.

The forge is only believable if the wall of the forge is itself witnessed.

This module defines the chip boundary:
  1. STAGED    — raw input frozen as immutable blob with capture envelope
  2. ON-CHIP   — blob crosses measured boundary into transform chamber
  3. OFF-CHIP  — chamber emits authoritative governed state with receipts
  4. INTERPRETED — projections derived from authoritative state (outside boundary)

Perception modes:
  heuristic     — deterministic clause-level smell parser, inside the chamber
  model_local   — local model proposes, proposals enter chamber as staged input
  model_remote  — remote model proposes (network call), proposals enter as staged input

  In heuristic mode, perception is INSIDE the chamber boundary.
  In model-assisted modes, the model runs OUTSIDE the boundary. The model
  is perimeter I/O, like witness_collector.py. It proposes. The chamber decides.

Execution classes:
  deterministic — all transforms pure, same input → same output, always
  model         — at least one model-executed step (perception was model-assisted)
  mixed         — deterministic judgment + model-assisted perception
  human         — human ratification involved (future)

Wall states (computed, not declared):
  held    — all deterministic, only approved modules, no undeclared deps/network
  soft    — model-local perception (non-deterministic perception, deterministic judgment)
  open    — model-remote perception (network call in the pipeline)
  broken  — undeclared dependency, unsigned transform, or failed verification

Security modes (orthogonal to wall state):
  Mode 0: Soft Boundary — source hashes only, no hardware attestation
  Mode 1: Measured Boundary — process-isolated, config-bound
  Mode 2: Attested Boundary — TEE/enclave/signed execution environment (future)

The chamber is not the transforms. The chamber is the wall around the transforms.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .stamp import stamp, Stamp, h, _canonical_json, verify_stamp, GENESIS


# ---------------------------------------------------------------------------
# Blob lifecycle stages
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StagedBlob:
    """A raw input frozen before interpretation.

    Immutable. Content-addressed. The exact bytes we accepted.
    This is the ground truth for what entered the system.
    """
    blob_hash: str          # SHA-256 of raw bytes
    raw_bytes: bytes        # the actual payload
    byte_length: int        # len(raw_bytes)
    content_type: str       # "text/plain", "application/json", etc.
    capture_source: str     # "paste", "share", "import", "api"
    capture_agent: str      # device/user/system identifier
    capture_ts: str         # ISO timestamp of capture
    source_metadata: dict   # optional: filename, thread_id, etc.


@dataclass(frozen=True)
class BoundaryAttestation:
    """Proof that a staged blob crossed into a measured chamber.

    Binds the blob to the chamber configuration at the moment of ingress.
    This is the wall receipt — separate from the transform receipts inside.

    perception_mode and execution_class are proof-bearing — they are
    hashed into the attestation_hash. Changing the mode changes the
    attestation. You cannot splice a model-assisted result into a
    heuristic attestation.
    """
    blob_hash: str          # hash of the staged blob
    chamber_hash: str       # hash of the chamber package
    perceiver_hash: str     # hash of the tagger source
    kernel_hash: str        # hash of the type system + sieve source
    config_hash: str        # hash of any runtime config
    perception_mode: str    # "heuristic", "model_local", "model_remote"
    execution_class: str    # "deterministic", "model", "mixed", "human"
    security_mode: str      # "soft", "measured", "attested"
    ingress_ts: str         # when the blob entered the chamber
    attestation_hash: str   # self-hash over all above fields


@dataclass(frozen=True)
class AuthoritativeOutput:
    """The governed state emitted by the chamber.

    This is the off-chip result. It is authoritative.
    Projections derive from this. They are not this.
    """
    governed_state: dict    # promoted/contested/deferred/loss
    receipt_chain: list     # stamps from the transform pipeline
    output_hash: str        # SHA-256 of canonical governed state
    attestation_hash: str   # back-reference to boundary attestation
    inscription_tx: str     # tx_log entry hash from inscription


# ---------------------------------------------------------------------------
# Wall state computation
# ---------------------------------------------------------------------------

def compute_wall_state(
    perception_mode: str,
    execution_class: str,
    security_mode: str,
    verification_errors: list[str] | None = None,
) -> str:
    """Compute wall state from execution properties. Pure.

    Explicit rules:
      held   — heuristic perception + deterministic execution + no verification errors
      soft   — model_local perception (non-deterministic perception, deterministic judgment)
      open   — model_remote perception (network dependency in pipeline)
      broken — verification errors, or undeclared execution class

    Wall state is computed, not declared. The chamber looks at what
    actually ran and derives the state.
    """
    # Any verification error breaks the wall
    if verification_errors:
        return "broken"

    # Unknown or undeclared execution class breaks the wall
    if execution_class not in ("deterministic", "model", "mixed", "human"):
        return "broken"

    # Perception mode determines the wall
    if perception_mode == "heuristic" and execution_class == "deterministic":
        return "held"
    elif perception_mode == "model_local":
        return "soft"
    elif perception_mode == "model_remote":
        return "open"
    elif execution_class == "model" or execution_class == "mixed":
        return "soft"
    elif execution_class == "human":
        return "soft"  # human ratification is trusted but not deterministic

    # Default: if we can't determine, be honest
    return "soft"


def derive_execution_class(perception_mode: str) -> str:
    """Derive execution class from perception mode. Pure.

    heuristic    → deterministic (all transforms are pure functions)
    model_local  → mixed (model perception + deterministic judgment)
    model_remote → mixed (model perception + deterministic judgment + network)
    """
    if perception_mode == "heuristic":
        return "deterministic"
    elif perception_mode in ("model_local", "model_remote"):
        return "mixed"
    return "deterministic"  # default safe


# ---------------------------------------------------------------------------
# Stage a blob
# ---------------------------------------------------------------------------

def stage_blob(
    raw: bytes | str,
    *,
    content_type: str = "text/plain",
    capture_source: str = "paste",
    capture_agent: str = "",
    source_metadata: dict | None = None,
) -> StagedBlob:
    """Freeze raw input as an immutable staged blob.

    This is step 1 of the blob lifecycle. The raw bytes are hashed
    and the capture envelope is constructed. Nothing is interpreted yet.

    The blob hash is the ground truth for what entered the system.
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8")

    return StagedBlob(
        blob_hash=h(raw),
        raw_bytes=raw,
        byte_length=len(raw),
        content_type=content_type,
        capture_source=capture_source,
        capture_agent=capture_agent or _default_agent(),
        capture_ts=_now_iso(),
        source_metadata=source_metadata or {},
    )


# ---------------------------------------------------------------------------
# Measure the chamber
# ---------------------------------------------------------------------------

def measure_chamber() -> dict[str, str]:
    """Compute the chamber measurement — hashes of all transform code.

    This is the "what version of the forge is running" measurement.
    In Mode 0 (soft boundary), this hashes source files.
    In Mode 2 (attested), this would be a TEE quote.
    """
    surface_dir = Path(__file__).parent

    # Hash each trunk module
    perceiver_hash = _hash_file(surface_dir / "epistemic_tagger.py")
    types_hash = _hash_file(surface_dir / "mother_types.py")
    sieve_hash = _hash_file(surface_dir / "sieve.py")
    stamp_hash_val = _hash_file(surface_dir / "stamp.py")
    receipted_hash = _hash_file(surface_dir / "receipted.py")
    analyzer_hash = _hash_file(surface_dir / "analyzer.py")
    code_perception_hash = _hash_file(surface_dir / "code_perception.py")

    # Kernel hash = hash of all trunk module hashes combined
    kernel_hash = h(_canonical_json({
        "perceiver": perceiver_hash,
        "types": types_hash,
        "sieve": sieve_hash,
        "stamp": stamp_hash_val,
        "receipted": receipted_hash,
        "analyzer": analyzer_hash,
        "code_perception": code_perception_hash,
    }).encode())

    # Chamber hash = kernel + chamber module itself
    chamber_self_hash = _hash_file(surface_dir / "chamber.py")
    chamber_hash = h(_canonical_json({
        "kernel": kernel_hash,
        "chamber": chamber_self_hash,
    }).encode())

    return {
        "chamber_hash": chamber_hash,
        "kernel_hash": kernel_hash,
        "perceiver_hash": perceiver_hash,
        "types_hash": types_hash,
        "sieve_hash": sieve_hash,
        "stamp_hash": stamp_hash_val,
        "receipted_hash": receipted_hash,
        "analyzer_hash": analyzer_hash,
        "code_perception_hash": code_perception_hash,
    }


# ---------------------------------------------------------------------------
# Boundary attestation — the wall receipt
# ---------------------------------------------------------------------------

def attest_boundary(
    staged: StagedBlob,
    chamber: dict[str, str],
    *,
    config: dict | None = None,
    perception_mode: str = "heuristic",
    security_mode: str = "soft",
) -> BoundaryAttestation:
    """Create a boundary attestation: proof that this blob entered this chamber.

    This is the receipt for the wall, not for the transforms inside.
    The transform receipts (stamps) are separate and live inside the chamber.

    perception_mode and execution_class are proof-bearing — they are
    hashed into the attestation. You cannot splice a model-assisted
    result into a heuristic attestation.
    """
    config_hash = h(_canonical_json(config or {}).encode())
    execution_class = derive_execution_class(perception_mode)

    partial = {
        "blob_hash": staged.blob_hash,
        "chamber_hash": chamber["chamber_hash"],
        "perceiver_hash": chamber["perceiver_hash"],
        "kernel_hash": chamber["kernel_hash"],
        "config_hash": config_hash,
        "perception_mode": perception_mode,
        "execution_class": execution_class,
        "security_mode": security_mode,
        "ingress_ts": _now_iso(),
    }
    attestation_hash = h(_canonical_json(partial).encode())

    return BoundaryAttestation(
        blob_hash=staged.blob_hash,
        chamber_hash=chamber["chamber_hash"],
        perceiver_hash=chamber["perceiver_hash"],
        kernel_hash=chamber["kernel_hash"],
        config_hash=config_hash,
        perception_mode=perception_mode,
        execution_class=execution_class,
        security_mode=security_mode,
        ingress_ts=partial["ingress_ts"],
        attestation_hash=attestation_hash,
    )


# ---------------------------------------------------------------------------
# Full chip lifecycle
# ---------------------------------------------------------------------------

def process_through_chamber(
    raw: bytes | str,
    *,
    topic_handle: str = "default",
    topic_keywords: set[str] | None = None,
    capture_source: str = "paste",
    capture_agent: str = "",
    source_metadata: dict | None = None,
    config: dict | None = None,
    perception_mode: str = "heuristic",
    security_mode: str = "soft",
    db_path: str | None = None,
) -> dict[str, Any]:
    """The full blob lifecycle: staged → on-chip → off-chip → ready for interpretation.

    Returns the complete custody record:
      - staged_blob: the frozen input
      - boundary_attestation: proof of chamber ingress
      - authoritative_output: the governed state + receipts
      - chamber_measurement: what version of the forge ran
      - security_mode: what level of boundary proof exists

    Projections (consumer cards, pro cards) are NOT part of this output.
    They are derived downstream, outside the chamber boundary.
    """
    from .pipeline import analyze_thread

    # 1. STAGE — freeze the raw input
    staged = stage_blob(
        raw,
        content_type="text/plain",
        capture_source=capture_source,
        capture_agent=capture_agent,
        source_metadata=source_metadata,
    )

    # 2. MEASURE — hash the chamber
    chamber = measure_chamber()

    # 3. ATTEST BOUNDARY — prove this blob entered this chamber
    # Reject model modes until real proposal paths exist.
    # When perception_mode is "model_local" or "model_remote", the chamber
    # would need a distinct execution path (external model proposes, chamber
    # admits). That path doesn't exist yet. Claiming it ran would be dishonest.
    _IMPLEMENTED_PERCEPTION_MODES = {"heuristic"}
    if perception_mode not in _IMPLEMENTED_PERCEPTION_MODES:
        raise ValueError(
            f"perception_mode={perception_mode!r} is not yet implemented. "
            f"Available modes: {sorted(_IMPLEMENTED_PERCEPTION_MODES)}. "
            f"Model-assisted perception requires an external proposal path "
            f"that feeds pre-classified clauses into the chamber."
        )

    # Reject security modes that require evidence we can't produce.
    # "soft" is source hashes only (what we have). "measured" and "attested"
    # require process isolation or TEE quotes that don't exist yet.
    _IMPLEMENTED_SECURITY_MODES = {"soft"}
    if security_mode not in _IMPLEMENTED_SECURITY_MODES:
        raise ValueError(
            f"security_mode={security_mode!r} is not yet implemented. "
            f"Available modes: {sorted(_IMPLEMENTED_SECURITY_MODES)}. "
            f"'measured' requires process isolation. 'attested' requires TEE/enclave."
        )

    attestation = attest_boundary(
        staged, chamber,
        config=config,
        perception_mode=perception_mode,
        security_mode=security_mode,
    )

    # 4. ON-CHIP — run the transform pipeline
    text = raw if isinstance(raw, str) else raw.decode("utf-8")
    pipeline_result = analyze_thread(
        text,
        topic_handle=topic_handle,
        topic_keywords=topic_keywords,
        db_path=db_path,
    )

    # 5. OFF-CHIP — package the authoritative output
    governed_state = pipeline_result["governed_state"]
    output_hash = h(_canonical_json(governed_state).encode())

    # 5b. Mint a final governance stamp that binds the sieve chain tip
    # to the full governed state. The sieve stamp hashes only the sieve
    # output (promoted/contested/deferred/loss). The governed state also
    # includes typed_units and classification. This stamp closes the gap.
    pipeline_stamps = pipeline_result["receipt_chain"]["stamps"]
    sieve_tip = pipeline_stamps[-1]["stamp_hash"] if pipeline_stamps else GENESIS
    governance_stamp = stamp(
        "governance",
        sieve_tip,                      # input = sieve chain tip
        chamber["kernel_hash"],         # fn = the whole kernel
        output_hash,                    # output = full governed state
        sieve_tip,                      # prev = sieve chain tip (extends the chain)
    )
    governance_stamp_dict = {
        "schema": governance_stamp.schema,
        "domain": governance_stamp.domain,
        "input_hash": governance_stamp.input_hash,
        "fn_hash": governance_stamp.fn_hash,
        "output_hash": governance_stamp.output_hash,
        "prev_stamp_hash": governance_stamp.prev_stamp_hash,
        "stamp_hash": governance_stamp.stamp_hash,
    }
    # Extend the receipt chain with the governance stamp
    all_stamps = list(pipeline_stamps) + [governance_stamp_dict]

    # 6. INSCRIBE — if store path provided, write the full custody record
    inscription_tx = ""
    receipt_chain_full = {
        "stamps": all_stamps,
        "chain_length": len(all_stamps),
        "tip_hash": governance_stamp.stamp_hash,
    }
    if db_path:
        inscription_tx = _inscribe_custody(
            db_path, staged, attestation, governed_state,
            receipt_chain_full, output_hash,
        )

    return {
        "staged_blob": {
            "blob_hash": staged.blob_hash,
            "byte_length": staged.byte_length,
            "content_type": staged.content_type,
            "capture_source": staged.capture_source,
            "capture_agent": staged.capture_agent,
            "capture_ts": staged.capture_ts,
            "source_metadata": staged.source_metadata,
        },
        "boundary_attestation": {
            "blob_hash": attestation.blob_hash,
            "chamber_hash": attestation.chamber_hash,
            "perceiver_hash": attestation.perceiver_hash,
            "kernel_hash": attestation.kernel_hash,
            "config_hash": attestation.config_hash,
            "perception_mode": attestation.perception_mode,
            "execution_class": attestation.execution_class,
            "security_mode": attestation.security_mode,
            "ingress_ts": attestation.ingress_ts,
            "attestation_hash": attestation.attestation_hash,
        },
        "authoritative_output": {
            "governed_state": governed_state,
            "output_hash": output_hash,
            "receipt_chain": receipt_chain_full,
            "attestation_hash": attestation.attestation_hash,
            "inscription_tx": inscription_tx,
        },
        "chamber_measurement": chamber,
        "perception_mode": perception_mode,
        "execution_class": derive_execution_class(perception_mode),
        "wall_state": compute_wall_state(
            perception_mode,
            derive_execution_class(perception_mode),
            security_mode,
        ),
        "security_mode": security_mode,
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_custody(record: dict[str, Any]) -> dict[str, Any]:
    """Verify a custody record end-to-end.

    Nine checks — binding across the full chain from blob to governed state:

    1. Boundary attestation self-hash is valid
    2. Chamber measurement matches attestation
    3. Staged blob hash matches attestation blob hash (wall→blob binding)
    4. Attestation blob hash matches first stamp's input origin (blob→chain binding)
    5. Receipt chain is fully linked (prev_stamp_hash chain)
    6. Each stamp's self-hash is valid
    7. Each pipeline stamp's fn_hash matches its chamber measurement (chain→chamber binding)
    8. Final governance stamp's output_hash matches authoritative output hash (chain→output binding)
    9. Output hash matches governed state (output→state binding)

    No green light until all nine pass.
    """
    errors = []

    # 1. Verify boundary attestation self-hash
    att = record["boundary_attestation"]
    partial = {
        "blob_hash": att["blob_hash"],
        "chamber_hash": att["chamber_hash"],
        "perceiver_hash": att["perceiver_hash"],
        "kernel_hash": att["kernel_hash"],
        "config_hash": att["config_hash"],
        "perception_mode": att.get("perception_mode", "heuristic"),
        "execution_class": att.get("execution_class", "deterministic"),
        "security_mode": att["security_mode"],
        "ingress_ts": att["ingress_ts"],
    }
    recomputed = h(_canonical_json(partial).encode())
    if recomputed != att["attestation_hash"]:
        errors.append("boundary attestation hash mismatch")

    # 2. Verify chamber measurement matches attestation
    chamber = record.get("chamber_measurement", {})
    if chamber.get("chamber_hash") != att["chamber_hash"]:
        errors.append("chamber hash does not match attestation")

    # 2b. Enforce execution_class invariant:
    # execution_class must equal derive_execution_class(perception_mode)
    # This prevents someone from re-signing an attestation with a
    # different execution_class to change the wall_state.
    attested_mode = att.get("perception_mode", "heuristic")
    attested_class = att.get("execution_class", "deterministic")
    expected_class = derive_execution_class(attested_mode)
    if attested_class != expected_class:
        errors.append(
            f"execution_class invariant violated: "
            f"perception_mode={attested_mode} requires execution_class={expected_class}, "
            f"got {attested_class}"
        )

    # 3. Staged blob hash matches attestation blob hash
    staged = record.get("staged_blob", {})
    if staged.get("blob_hash") and staged["blob_hash"] != att["blob_hash"]:
        errors.append("staged blob hash does not match attestation")

    # Get the receipt chain
    auth = record["authoritative_output"]
    chain = auth.get("receipt_chain", {})
    stamps = chain.get("stamps", [])

    # Map domain → expected fn_hash from chamber measurement
    _domain_to_chamber_hash = {
        "tagger": chamber.get("perceiver_hash"),
        "mother_types": chamber.get("types_hash"),
        "sieve": chamber.get("sieve_hash"),
        "governance": chamber.get("kernel_hash"),
    }

    if stamps:
        # 4. Blob→chain binding: staged blob hash == first stamp's input_hash
        first_stamp = stamps[0]
        staged_blob_hash = att["blob_hash"]
        tagger_input = first_stamp.get("input_hash", "")
        if tagger_input != staged_blob_hash:
            errors.append(
                f"blob→chain binding broken: staged blob {staged_blob_hash[:16]}... "
                f"!= first stamp input {tagger_input[:16]}..."
            )

        # 5 + 6. Full chain linkage AND self-hash validity
        prev = GENESIS
        for i, s in enumerate(stamps):
            stmp = Stamp(**s)
            domain = s.get("domain", "?")

            # 6. Self-hash
            if not verify_stamp(stmp):
                errors.append(f"stamp {i} ({domain}) self-hash invalid")

            # 5. Chain linkage
            if stmp.prev_stamp_hash != prev:
                errors.append(
                    f"stamp {i} ({domain}) chain broken: "
                    f"expected prev={prev[:16]}..., got {stmp.prev_stamp_hash[:16]}..."
                )
            prev = stmp.stamp_hash

            # 7. fn_hash→chamber binding: each stamp's fn_hash must match
            # the chamber measurement for that domain
            expected_fn = _domain_to_chamber_hash.get(domain)
            if expected_fn and stmp.fn_hash != expected_fn:
                errors.append(
                    f"stamp {i} ({domain}) fn_hash does not match chamber: "
                    f"stamp={stmp.fn_hash[:16]}... chamber={expected_fn[:16]}..."
                )

        # 8. Final stamp's output_hash == authoritative output_hash
        # The governance stamp (last in chain) binds the full governed state
        last_stamp = stamps[-1]
        last_output = last_stamp.get("output_hash", "")
        if last_output != auth["output_hash"]:
            errors.append(
                f"chain→output binding broken: last stamp output={last_output[:16]}... "
                f"!= authoritative output={auth['output_hash'][:16]}..."
            )
    else:
        errors.append("no stamps in receipt chain")

    # 9. Output hash matches governed state
    gs = auth["governed_state"]
    recomputed_output = h(_canonical_json(gs).encode())
    if recomputed_output != auth["output_hash"]:
        errors.append("output hash does not match governed state")

    # Compute wall state from what we found
    perception_mode = att.get("perception_mode", record.get("perception_mode", "heuristic"))
    execution_class = att.get("execution_class", record.get("execution_class", "deterministic"))
    security_mode = record.get("security_mode", "unknown")
    wall_state = compute_wall_state(perception_mode, execution_class, security_mode, errors or None)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "checks_performed": 9,
        "perception_mode": perception_mode,
        "execution_class": execution_class,
        "wall_state": wall_state,
        "security_mode": security_mode,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    """SHA-256 of file contents."""
    return h(path.read_bytes())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_agent() -> str:
    import platform
    return f"{platform.node()}:{os.getpid()}"


def _inscribe_custody(
    db_path: str,
    staged: StagedBlob,
    attestation: BoundaryAttestation,
    governed_state: dict,
    receipt_chain: dict,
    output_hash: str,
) -> str:
    """Inscribe the full custody record to the store.

    Writes:
    1. Staged blob (content-addressed)
    2. Authoritative output blob (content-addressed)
    3. Boundary stamp (a proper stamp for the chamber crossing)
    4. Attestation fact (linked to the boundary stamp)
    5. Governed state facts (linked to pipeline stamps)
    6. Custody tx log entry
    """
    from .store import init_db, blob_write, stamp_write, fact_write, tx_append
    from .stamp import stamp as mint_stamp, GENESIS, Stamp

    conn = init_db(db_path)

    # 1. Write the staged blob
    blob_write(conn, staged.raw_bytes)

    # 2. Write the authoritative output as a blob
    output_blob = _canonical_json(governed_state).encode()
    output_blob_ref = blob_write(conn, output_blob)

    # 3. Mint and write a boundary stamp — this is a real stamp, not a fake hash
    # The boundary stamp binds: input=blob_hash, fn=chamber_hash, output=output_hash
    boundary_stamp = mint_stamp(
        "chamber_boundary",
        attestation.blob_hash,          # what went in
        attestation.chamber_hash,       # which chamber ran
        output_hash,                    # what came out
        GENESIS,                        # chamber boundary is root of its own chain
    )
    stamp_write(
        conn, boundary_stamp,
        input_blob_ref=staged.blob_hash,
        output_blob_ref=output_blob_ref,
    )

    # 4. Write attestation as a fact linked to the boundary stamp
    fact_write(
        conn,
        f"att_{attestation.attestation_hash[:12]}",
        boundary_stamp.stamp_hash,      # fact references the real stamp
        "chamber",
        "boundary_attestation",
        _canonical_json({
            "blob_hash": attestation.blob_hash,
            "chamber_hash": attestation.chamber_hash,
            "perceiver_hash": attestation.perceiver_hash,
            "kernel_hash": attestation.kernel_hash,
            "config_hash": attestation.config_hash,
            "security_mode": attestation.security_mode,
            "attestation_hash": attestation.attestation_hash,
        }),
    )

    # 5. Write pipeline stamps if present
    stamps = receipt_chain.get("stamps", [])
    for s_dict in stamps:
        stmp = Stamp(**s_dict)
        stamp_write(conn, stmp)

    # 6. Write custody tx
    tx_hash = tx_append(conn, "custody.inscribed", "chamber", boundary_stamp.stamp_hash)
    conn.commit()
    conn.close()

    return tx_hash
