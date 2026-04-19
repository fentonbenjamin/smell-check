# Smell Check

Smell detection for code.

## What it does

Give it text — a group chat, email thread, PR discussion, code snippet — and it tells you what smells:

- **Findings** — what smells funny and why
- **Stable points** — what looks decided or reported
- **Open questions** — what's still unclear or unresolved

Every judgment is receipted inside a measured chamber. The verification result tells you whether the analysis is intact.

## How it works

```
thread/conversation → epistemic tagger (clause cues) → sieve
code/diff           → analyzer (AST)  → code adapter  → sieve
                                                          ↓
                                                    smell_check output
```

## Run locally

```bash
# Clone
git clone https://github.com/benfen/smell-check.git
cd smell-check

# Install
python3 -m venv .venv
.venv/bin/pip install pydantic "mcp>=1.2.0" pytest

# Test
.venv/bin/pytest tests/ -q

# Run HTTP server
.venv/bin/python -m surface.gateway

# Run MCP server (stdio, for Claude Code)
.venv/bin/python -m surface.gateway --stdio
```

## Claude Code MCP config

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "smell-check": {
      "type": "stdio",
      "command": "/path/to/smell-check/.venv/bin/python",
      "args": ["-m", "surface.gateway", "--stdio"],
      "cwd": "/path/to/smell-check"
    }
  }
}
```

Then in Claude Code: "smell check this thread" or "smell check this code."

## API

### HTTP

```bash
# Analyze
curl -X POST http://localhost:8800/threads/analyze \
  -H 'Content-Type: application/json' \
  -d '{"text": "We decided Friday. Not sure about parking."}'

# Verify
curl -X POST http://localhost:8800/threads/verify \
  -H 'Content-Type: application/json' \
  -d @result.json

# Health
curl http://localhost:8800/health
```

### MCP

Tool: `smell_check`

```
smell_check(text="...", topic="optional-label")
```

Returns: summary, findings, stable_points, open_questions, verification, custody_record.

## What's inside

- `epistemic_tagger.py` — clause-level smell parser for threads/conversations
- `analyzer.py` + `code_perception.py` — AST-based smell detection for Python code
- `mother_types.py` — five governance types: CONTRACT, CONSTRAINT, UNCERTAINTY, RELATION, WITNESS
- `sieve.py` — deterministic promotion/judgment engine
- `chamber.py` — measured execution boundary with 9-check custody verification
- `stamp.py` — receipted pure transform primitive
- `store.py` — SQLite dual inscription (blobs + facts + tx log)
- `gateway.py` — HTTP + MCP server
- `pipeline.py` — thread analysis pipeline
- `projections.py` — findings-first output rendering

## Properties

- **Pure** — no model calls
- **Deterministic** — same input, same output, always

## 117 tests

```
tests/test_gateway.py       — smell_check tool, verify loop, MCP registration
tests/test_chamber.py       — blob staging, attestation, custody verification, tamper detection
tests/test_pipeline.py      — thread analysis, multi-turn, projections
tests/test_pipeline_determinism.py — same input same stamps
tests/test_receipt_integrity.py — tampered payload detection
tests/test_store.py          — SQLite store, blobs, stamps, facts, tx chain
```
