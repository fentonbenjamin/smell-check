# CLAUDE.md

## What this is

Smell Check is a deterministic code smell detector. One MCP tool (`smell_check`), two perception lanes, one judgment layer.

## Commands

```bash
# Install
pip install -e ".[dev]"

# Run tests
pytest tests/ -q

# Run HTTP server
python -m smell_check.gateway

# Run MCP server (stdio)
python -m smell_check.gateway --stdio
```

## Architecture

Two perception lanes feed the same judgment layer:

- **Thread lane** (`epistemic_tagger.py`): clause-level cue parsing for conversations/prose
- **Code lane** (`analyzer.py` → `code_perception.py`): AST-based structural analysis for Python source
- **Mixed input**: both lanes fire, findings merge through the sieve

Downstream law (same for both lanes):
- `mother_types.py` — five governance types: CONTRACT, CONSTRAINT, UNCERTAINTY, RELATION, WITNESS
- `sieve.py` — deterministic promotion engine with anchor-aware dedup
- `projections.py` — findings-first rendering

Proof path:
- `stamp.py` — receipt primitive
- `receipted.py` — receipted transform orchestrators
- `chamber.py` — measured execution boundary, 9-check custody verification
- `store.py` — SQLite dual inscription

Gateway:
- `gateway.py` — HTTP + MCP faces, one tool: `smell_check`

## Key invariants

- **Pure**: no model calls, no network, no external APIs in the trunk
- **Deterministic**: same input → same output, always
- **AST-primary for code**: code findings come from AST analysis, not prose pattern matching
- **Anchor-aware dedup**: different functions with similar words are not duplicates
- **Lane separation**: code input runs code lane only, thread input runs thread lane only, mixed runs both
- **Receipted**: every transform is stamped, custody is verifiable

## What NOT to do

- Do not add model/LLM calls to the perception layer
- Do not collapse the two-lane model into one generic pipeline
- Do not let the thread lane fire on docstrings/comments in code input
- Do not let code findings bypass the sieve
- Do not render code findings with thread-lane language ("someone needs to act", "looks decided")
- Do not flag local/relative imports as external provenance gaps

## Test expectations

148 tests across 8 test files. All must pass before any PR.

Key test files:
- `test_gateway.py` — smell_check tool, verify loop, MCP registration
- `test_chamber.py` — blob staging, attestation, custody verification, tamper detection
- `test_code_smell.py` — code lane regression: no prose cues on code, purity not contested, local imports not flagged
- `test_mixed_input.py` — combinatory lane behavior: PR-shaped mixed input
- `test_pipeline.py` — thread analysis, multi-turn, projections
- `test_pipeline_determinism.py` — same input same stamps
- `test_receipt_integrity.py` — tampered payload detection
- `test_store.py` — SQLite store round-trip

## Output shape

```json
{
  "summary": "...",
  "findings": [{"judgment": "...", "because": "...", "where": {...}, "what_to_do": "...", "drillback": {...}}],
  "stable_points": [{"judgment": "...", "because": "...", "where": {...}, "drillback": {...}}],
  "open_questions": [{"judgment": "...", "because": "...", "where": {...}, "what_to_do": "...", "drillback": {...}}],
  "receipt_status": {"wall": "held", "valid": true, "checks": 9, ...},
  "verification": {...},
  "custody_record": {...}
}
```

## Current work priorities

1. Widen code perception: add more AST-based senses (test_gap, guard_removed, interface_drift)
2. Tighten rendering: findings should read like a sharp code review, not templates
3. Improve mixed-input handling: strip fenced block noise from thread lane
4. Add gold test fixtures from real repos
5. Anchor-aware dedup tuning for code findings
