# Chamber Kernel Specification

**Date:** 2026-04-22
**Status:** Design spec for review
**Context:** Smell Check v0 ships Python-only (Mode 0). This spec defines the Rust chamber kernel + Swift signer + harness that upgrade it to Mode 1-2.

## Architecture

```
Python perception perimeter (untrusted)
  → produces canonical_packet (JSON)

Harness (trusted ceremony runner)
  → freezes inputs
  → launches measured Rust kernel
  → enforces runtime posture (no network, no plugins)
  → captures kernel output
  → passes receipt to Swift signer

Rust chamber kernel (the law)
  → deserializes canonical_packet
  → validates against chamber_manifest
  → evaluates recipe_card policy
  → computes wall_state
  → emits receipt if eligible
  → rejects and downgrades if not

Swift soft chip (the seal)
  → receives receipt from harness
  → signs with SecureEnclave.P256 if wall_state permits
  → emits signed_receipt
```

### Trust boundaries

| Boundary | What crosses | Trust direction |
|----------|-------------|-----------------|
| Python → Harness | canonical_packet JSON file | Untrusted → Trusted. Harness treats packet as specimen. |
| Harness → Rust kernel | canonical_packet on stdin | Trusted → Measured. Kernel is the law. |
| Rust kernel → Harness | receipt JSON on stdout | Measured → Trusted. Harness captures receipt. |
| Harness → Swift signer | receipt digest | Trusted → Hardware. Signer checks wall_state. |
| Swift signer → World | signed_receipt | Hardware → Verifier. Signature is non-forgeable. |

### Key principle

**Python is specimen, not authority.** Everything Python sends is untrusted input. The Rust kernel re-hashes, re-validates, and rejects undeclared fields. Python proposes. Rust decides.

---

## Schema 1: Canonical Packet

The canonical packet is what Python produces and what the Rust kernel consumes. It crosses the trust boundary between the perception layer and the measured chamber.

```json
{
  "$schema": "chamber.canonical_packet.v1",

  "input": {
    "blob_hash": "sha256 hex — hash of the raw input bytes",
    "byte_length": 1234,
    "content_type": "text/plain | application/python | text/x-diff",
    "capture_source": "paste | share | api | cli",
    "capture_agent": "device:pid or user identifier"
  },

  "perception": {
    "perception_mode": "heuristic",
    "input_kind": "thread | python_source | diff | mixed",
    "tagger_fn_hash": "sha256 hex — hash of the tagger source that ran",
    "sieve_fn_hash": "sha256 hex — hash of the sieve source that ran",
    "analyzer_fn_hash": "sha256 hex — hash of the analyzer source (if code lane ran)",
    "pipeline_fn_hash": "sha256 hex — hash of pipeline.py"
  },

  "result": {
    "governed_state_hash": "sha256 hex — hash of canonical governed_state JSON",
    "promoted_count": 5,
    "contested_count": 0,
    "deferred_count": 1,
    "loss_count": 2,
    "finding_count": 3,
    "stable_count": 2,
    "open_question_count": 1
  },

  "chain": {
    "stamp_chain_tip": "sha256 hex — tip of the Python receipt chain",
    "stamp_chain_length": 4,
    "prev_chamber_receipt": "sha256 hex or null — prior chamber receipt if chaining"
  },

  "metadata": {
    "timestamp": "ISO 8601",
    "smell_check_version": "0.1.0",
    "python_version": "3.13.5",
    "platform": "darwin-arm64"
  }
}
```

### Validation rules (enforced by Rust kernel)

1. All hash fields must be 64-character lowercase hex (SHA-256).
2. `perception_mode` must be in the manifest's approved perception modes.
3. `input_kind` must be in `["thread", "python_source", "diff", "mixed"]`.
4. `tagger_fn_hash`, `sieve_fn_hash` must match the manifest's approved fn_hashes, OR the kernel downgrades wall_state.
5. `governed_state_hash` must be present and non-empty.
6. No undeclared fields. Unknown keys cause rejection.
7. `byte_length` must be > 0 and < manifest's max_input_size.

### What the kernel does NOT receive

- The raw input text (too large, untrusted content)
- The full governed_state (too large, only the hash matters)
- The projection/rendering output (that's outside the boundary)
- Any Python objects or pickled data

The kernel receives only hashes and metadata. It never sees the actual content. It decides whether the run qualifies based on the packet's structural properties.

---

## Schema 2: Chamber Manifest

The chamber manifest defines what the kernel is, what it approves, and what it measures. The manifest's own hash IS the chamber_id. Changing the manifest changes the chamber identity.

```json
{
  "$schema": "chamber.manifest.v1",

  "chamber_id": "sha256 hex — hash of this manifest (computed, not declared)",

  "identity": {
    "name": "smell-check-chamber",
    "version": "0.1.0",
    "kernel_binary_hash": "sha256 hex — hash of the compiled Rust binary",
    "kernel_source_hash": "sha256 hex — hash of the kernel source tree"
  },

  "approved_domains": ["smell_check"],

  "approved_perception_modes": ["heuristic"],

  "approved_execution_classes": ["deterministic"],

  "approved_fn_hashes": {
    "tagger": ["sha256 hex — one or more approved tagger versions"],
    "sieve": ["sha256 hex — one or more approved sieve versions"],
    "analyzer": ["sha256 hex — one or more approved analyzer versions"],
    "pipeline": ["sha256 hex — one or more approved pipeline versions"]
  },

  "policy": {
    "max_input_size": 1048576,
    "require_all_fn_hashes_approved": true,
    "allow_unknown_perception_mode": false,
    "allow_model_assisted": false,
    "require_signer": false
  },

  "wall_state_rules": {
    "held": {
      "require": [
        "perception_mode == heuristic",
        "execution_class == deterministic",
        "all fn_hashes approved",
        "no policy violations"
      ]
    },
    "soft": {
      "when": [
        "perception_mode == model_local",
        "OR execution_class == mixed"
      ]
    },
    "open": {
      "when": [
        "perception_mode == model_remote"
      ]
    },
    "broken": {
      "when": [
        "any validation failure",
        "unapproved fn_hash",
        "undeclared fields",
        "packet hash mismatch"
      ]
    }
  },

  "signer_policy": {
    "sign_on_held": true,
    "sign_on_soft": false,
    "sign_on_open": false,
    "sign_on_broken": false
  }
}
```

### Manifest invariants

1. `chamber_id` is computed by the kernel as `sha256(canonical_json(manifest without chamber_id))`. It is never caller-supplied.
2. The kernel binary hashes itself at startup and verifies against `kernel_binary_hash`. Mismatch = refuse to run.
3. `approved_fn_hashes` is a whitelist. Any fn_hash not in the list causes wall_state downgrade unless `require_all_fn_hashes_approved` is false.
4. The manifest is loaded once at kernel startup. It cannot be changed during a run.
5. The manifest is included in the audit bundle so verifiers can check what policy was active.

---

## Schema 3: Recipe Card

A recipe card defines the policy for a specific type of run. The manifest defines what the chamber IS. The recipe card defines what this run is FOR.

```json
{
  "$schema": "chamber.recipe_card.v1",

  "recipe_id": "sha256 hex — hash of this card",

  "name": "smell_check_code_review",
  "description": "Smell check a code review: thread + code + optional diff",

  "required_input_kinds": ["python_source"],
  "allowed_input_kinds": ["python_source", "diff", "mixed", "thread"],

  "required_perception_mode": "heuristic",

  "minimum_findings_for_authority": 0,
  "minimum_stable_for_authority": 0,

  "authority_rules": {
    "authoritative_if": [
      "wall_state == held",
      "input_kind in allowed_input_kinds",
      "perception_mode == required_perception_mode"
    ],
    "downgrade_if": [
      "wall_state != held",
      "input_kind not in allowed_input_kinds"
    ],
    "reject_if": [
      "wall_state == broken"
    ]
  },

  "output_requirements": {
    "must_include_receipt": true,
    "must_include_wall_state": true,
    "must_include_downgrade_reasons": true,
    "may_include_governed_state_hash": true
  }
}
```

### Recipe card usage

1. The harness loads exactly one recipe card per run.
2. The recipe card hash is included in the receipt, so the verifier knows what policy applied.
3. Recipe cards are versioned and immutable. A new policy = a new card with a new hash.
4. The kernel evaluates `authority_rules` against the packet and the manifest to produce the final `authoritative` yes/no decision.

---

## Schema 4: Chamber Receipt

What the Rust kernel emits. This is the output that crosses back through the harness to the signer.

```json
{
  "$schema": "chamber.receipt.v1",

  "receipt_hash": "sha256 hex — self-hash of this receipt",

  "chamber_id": "sha256 hex — from the manifest",
  "recipe_id": "sha256 hex — from the recipe card",

  "input": {
    "blob_hash": "sha256 hex — from the canonical packet",
    "governed_state_hash": "sha256 hex — from the canonical packet"
  },

  "stamps": {
    "python_chain_tip": "sha256 hex — the Python receipt chain tip",
    "chamber_stamp": {
      "domain": "chamber_authority",
      "input_hash": "sha256 hex — hash of the canonical packet",
      "fn_hash": "sha256 hex — kernel binary hash",
      "output_hash": "sha256 hex — hash of this receipt (pre-self-hash)",
      "prev_stamp_hash": "sha256 hex — python_chain_tip or genesis",
      "stamp_hash": "sha256 hex"
    }
  },

  "verdict": {
    "wall_state": "held | soft | open | broken",
    "authoritative": true,
    "execution_class": "deterministic",
    "perception_mode": "heuristic",
    "downgrade_reasons": []
  },

  "metadata": {
    "kernel_version": "0.1.0",
    "timestamp": "ISO 8601"
  }
}
```

### Receipt invariants

1. `receipt_hash` = `sha256(canonical_json(receipt without receipt_hash))`. Self-proving.
2. `chamber_stamp.fn_hash` = kernel binary hash = manifest's `kernel_binary_hash`. This proves which kernel produced the receipt.
3. `chamber_stamp.input_hash` = hash of the entire canonical packet. This binds the receipt to the exact input the kernel saw.
4. The receipt does NOT contain the governed state itself — only its hash. The governed state lives outside the boundary.
5. If `authoritative = false`, the receipt still exists but should not be signed.

---

## Schema 5: Signed Receipt

What the Swift signer produces. This is the final artifact that leaves the system.

```json
{
  "$schema": "chamber.signed_receipt.v1",

  "receipt": { "...the full chamber receipt..." },

  "signature": {
    "algorithm": "ECDSA-P256",
    "key_id": "SecureEnclave key identifier",
    "digest": "sha256 hex — hash of the receipt JSON",
    "signature_bytes": "base64 — the actual ECDSA signature",
    "signed_at": "ISO 8601"
  },

  "device": {
    "platform": "darwin-arm64",
    "os_version": "macOS 26.x",
    "secure_enclave": true
  }
}
```

### Signed receipt invariants

1. The signer ONLY signs if `receipt.verdict.wall_state` is in the manifest's `signer_policy` approved states.
2. The signature covers the receipt's canonical JSON, not just the receipt_hash. This prevents someone from swapping the receipt body and keeping the hash.
3. The key is SecureEnclave-backed and non-extractable. The signature proves physical device identity.
4. Verification requires the public key (exported once at key creation) and the receipt JSON.

---

## Schema 6: Audit Bundle

What the harness writes after a complete run. This is the full ceremony record.

```json
{
  "$schema": "chamber.audit_bundle.v1",

  "run_id": "unique run identifier",
  "timestamp": "ISO 8601",

  "manifest": { "...the chamber manifest..." },
  "recipe_card": { "...the recipe card..." },
  "canonical_packet": { "...the canonical packet..." },
  "chamber_receipt": { "...the chamber receipt..." },
  "signed_receipt": { "...the signed receipt, if signing occurred..." },

  "harness": {
    "harness_version": "0.1.0",
    "harness_binary_hash": "sha256 hex",
    "network_mode": "denied",
    "plugin_mode": "denied",
    "stdin_source": "file | pipe",
    "stdout_capture": "complete",
    "kernel_exit_code": 0,
    "kernel_stderr": ""
  },

  "bundle_hash": "sha256 hex — hash of everything above"
}
```

---

## Execution Plan

### Phase 1: Schemas and contracts (this document)
- Define canonical_packet, manifest, recipe_card, receipt, signed_receipt, audit_bundle
- Review with multiple models for feedback
- Freeze schemas before writing code

### Phase 2: Rust chamber kernel (~500 lines)
- Crate structure: `chamber-kernel/src/{main.rs, manifest.rs, packet.rs, policy.rs, receipt.rs}`
- Depends on existing `stamp.rs` and `core.rs` (copy or workspace)
- `#![forbid(unsafe_code)]`
- Reads manifest from file, packet from stdin, writes receipt to stdout
- Validates everything. Rejects undeclared fields. Re-hashes canonical JSON.
- Computes wall_state from rules, not declarations
- Single binary, reproducibly buildable

### Phase 3: Harness (~200 lines, Swift or Rust)
- Launches kernel binary in a subprocess
- Sets network deny (macOS sandbox-exec or App Sandbox)
- Feeds canonical packet on stdin
- Captures receipt from stdout
- Passes receipt to signer if wall_state qualifies
- Writes audit bundle

### Phase 4: Swift signer (~100 lines)
- SecureEnclave.P256 key generation (one-time)
- Public key export for verifiers
- Sign receipt digest
- Refuse to sign if wall_state doesn't qualify
- Can be a library called by the harness, or a separate tiny binary

### Phase 5: Python integration
- `smell_check` gateway gains `--chamber` mode
- Runs perception as normal
- Serializes canonical packet
- Invokes harness (subprocess or local binary)
- Returns signed receipt alongside findings

### Phase 6: Virgin Mac demo
- Fresh Mac, nothing installed except Xcode CLI tools and the binaries
- Paste code into the tool
- Python perceives, produces packet
- Harness invokes kernel
- Kernel stamps, computes wall_state = held
- Swift signs with SecureEnclave
- Out comes: findings + signed receipt proving the whole chain
- Verifier (on any machine with the public key) confirms the signature

---

## Open Questions

1. **Harness language:** Swift (natural for SecureEnclave access) or Rust (natural for the kernel ecosystem)? Could be Swift harness calling Rust kernel as subprocess.

2. **Canonical JSON spec:** Should we use RFC 8785 (JCS) for canonicalization, or keep the current `sort_keys=True, separators=(",",":")` convention? The Rust `core.rs` already implements the latter.

3. **Manifest distribution:** How does a verifier get the manifest to check a receipt? Bundle it in the audit bundle (current plan), publish it, or both?

4. **Key management:** SecureEnclave keys are device-bound. How does a verifier get the public key? First-use trust, published key registry, or certificate chain?

5. **Python packet construction trust:** Python constructs the canonical packet. The kernel re-hashes and validates, but it trusts the Python-reported fn_hashes. Should the kernel independently verify fn_hashes (e.g., by hashing the Python files itself), or is that the harness's job?

6. **Multi-transform runs:** The current spec is one packet → one receipt. Should the kernel support chained transforms (packet A → receipt A → packet B includes receipt A), or is chaining always done at the Python/harness level?

7. **Offline verification:** Can a receipt be verified without the kernel? Yes — it's just hash checks. But should there be a standalone `chamber-verify` CLI that checks receipts, signatures, and manifest consistency?
