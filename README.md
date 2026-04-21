# Smell Check

Before you merge, ship, or trust it, run Smell Check.

Smell Check is a deterministic pre-trust judgment tool for engineering work. It reads code, diffs, and discussion, then returns a short judgment about what smells off, what seems stable, and what still needs human attention.

It is not a coding agent. It does not call a model. It does not edit your files. It gives you a receipted result over the material you provide.

## What It Does

Smell Check helps answer:

- What smells off here?
- What seems stable enough to rely on?
- What is still unresolved?
- What should a human inspect before merge, ship, or action?

Current inputs work best as:

- Python code snippets or files
- diffs
- PR-shaped context with code plus discussion
- coordination threads, email threads, or review comments

## Quick Start

```bash
git clone https://github.com/fentonbenjamin/smell-check.git
cd smell-check

python3 -m venv .venv
.venv/bin/pip install pydantic "mcp>=1.2.0" pytest

.venv/bin/pytest tests/ -q
```

Run the local HTTP server:

```bash
.venv/bin/python -m smell_check.gateway
```

Run the local MCP server for Claude Code:

```bash
.venv/bin/python -m smell_check.gateway --stdio
```

## Claude Code Setup

Add Smell Check to your Claude Code MCP config:

```json
{
  "mcpServers": {
    "smell-check": {
      "type": "stdio",
      "command": "/absolute/path/to/smell-check/.venv/bin/python",
      "args": ["-m", "smell_check.gateway", "--stdio"],
      "cwd": "/absolute/path/to/smell-check"
    }
  }
}
```

Then ask Claude Code to use it in context:

```text
smell check this diff
smell check this file
smell check this PR thread
```

## HTTP API

The local HTTP API is for testing and simple integrations.

```bash
curl -s http://localhost:8800/health
```

```bash
curl -s http://localhost:8800/threads/analyze \
  -H 'Content-Type: application/json' \
  -d '{"text": "We decided Friday. Not sure about error handling.", "topic": "review"}'
```

```bash
curl -s http://localhost:8800/threads/verify \
  -H 'Content-Type: application/json' \
  -d @result.json
```

The `/threads/*` names are legacy local API names. The product surface is `smell_check`.

## MCP Tool

Smell Check currently exposes one MCP tool:

```text
smell_check(text: str, topic: str = "default")
```

The response includes:

- `summary`
- `findings`
- `stable_points`
- `open_questions`
- `receipt_status`
- `receipt_chain`
- `custody_record`

## Example Output Shape

```json
{
  "summary": "1 finding, 2 stable points, 1 open question.",
  "findings": [
    {
      "title": "Impure function: load_config",
      "because": "The function reads from disk, so callers should not treat it as pure.",
      "where": "config.py:12"
    }
  ],
  "stable_points": [
    "validate_config is structurally pure."
  ],
  "open_questions": [
    "Error handling is still unresolved in the surrounding discussion."
  ],
  "receipt_status": {
    "wall": "held",
    "valid": true
  }
}
```

Exact wording may change as the judgment layer improves.

## How It Works

Smell Check reads conversation and code differently, then combines what it finds into one deterministic result.

```text
conversation / thread -> clause cues
code / diff           -> AST + code signals
                                |
                                v
                         deterministic judgment
```

The current code lane is Python-first. The conversation lane is built for review comments, coordination threads, and PR discussion.

## Trust Model

Smell Check separates its deterministic judgment from the host LLM.

Each run produces a custody record that binds:

- the canonical input
- the transform identity
- the result
- the chamber measurement
- the wall-state verification

This lets you distinguish:

- what the host model says
- what Smell Check deterministically returned
- whether the returned record was later tampered with

The receipt proves integrity of the run. It does not prove the judgment is perfect.

## Current Scope

Supported today:

- local execution
- MCP stdio for Claude Code
- local HTTP server for testing
- thread/conversation lane
- Python code lane
- mixed prose plus code input
- receipt and tamper verification

Not supported yet:

- hosted service
- full repo indexing
- all programming languages
- background scanning
- remote chamber hosting
- polished PR app or browser UI

## Development

Run all tests:

```bash
.venv/bin/pytest tests/ -q
```

Current focused test areas:

- MCP and HTTP gateway behavior
- chamber custody and tamper checks
- thread pipeline behavior
- Python code-smell behavior
- mixed conversation plus code input
- deterministic output for repeat runs
- SQLite store and receipt chain behavior

## Internals

Most users do not need these files, but they are the main implementation pieces:

- `smell_check/gateway.py` — HTTP and MCP entrypoint
- `smell_check/chamber.py` — measured execution boundary and custody verification
- `smell_check/pipeline.py` — thread/conversation pipeline
- `smell_check/epistemic_tagger.py` — clause-level conversation parser
- `smell_check/analyzer.py` — AST-based Python analyzer
- `smell_check/code_perception.py` — code findings adapter
- `smell_check/sieve.py` — deterministic promotion and pattern engine
- `smell_check/projections.py` — user-facing output rendering
