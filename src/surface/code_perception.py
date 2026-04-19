"""Code perception lane — deterministic structural perception for source code.

This is the code-native parallel to epistemic_tagger.py.
The tagger smells threads. This smells code.

Architecture:
  thread/conversation → epistemic_tagger (clause cues) → mother_types → sieve
  code/diff           → analyzer (AST)    → code_perception adapter → sieve

Same judgment layer. Different perceiver.
The analyzer is the sense organ. This module is the adapter.

Pure. No I/O. No model calls.
"""

from __future__ import annotations

import re
from typing import Any

from .analyzer import analyze_source, extract_functions
from .mother_types import CONTRACT, CONSTRAINT, UNCERTAINTY, RELATION, WITNESS


# ---------------------------------------------------------------------------
# Input kind detection
# ---------------------------------------------------------------------------

def detect_input_kind(text: str) -> str:
    """Detect whether input is code, diff, thread, or mixed. Pure.

    Returns: "python_source", "diff", "thread", or "mixed"
    """
    lines = text.strip().split("\n")
    if not lines:
        return "thread"

    # Diff detection
    diff_signals = sum(1 for l in lines[:20] if l.startswith(("diff --git", "+++", "---", "@@", "+", "-")))
    if diff_signals > len(lines[:20]) * 0.3:
        return "diff"

    # Python source detection
    code_signals = 0
    for line in lines[:30]:
        stripped = line.strip()
        if stripped.startswith(("def ", "class ", "import ", "from ", "if __name__")):
            code_signals += 3
        elif stripped.startswith(("#", "'''", '"""')):
            code_signals += 1
        elif re.match(r"^\s+(return |yield |raise |pass$|self\.)", stripped):
            code_signals += 2
        elif re.match(r"^\s*\w+\s*=\s*", stripped):
            code_signals += 1

    if code_signals > len(lines[:30]) * 0.4:
        return "python_source"

    # Check for conversation signals
    thread_signals = sum(1 for l in lines[:20] if any(
        cue in l.lower() for cue in ("we decided", "not sure", "someone needs", "said", "agreed", "?")
    ))
    if thread_signals > 0:
        return "thread"

    # Default: if it looks like code but we're not sure, try code first
    if code_signals > 3:
        return "python_source"

    return "thread"


# ---------------------------------------------------------------------------
# Analyzer → typed findings adapter
# ---------------------------------------------------------------------------

def analyzer_to_findings(source: str, filename: str = "<input>") -> list[dict[str, Any]]:
    """Convert analyzer output to typed findings for the sieve. Pure.

    Each finding becomes a claim-shaped dict that the sieve can consume,
    with mother_type, text, and structural drillback.
    """
    try:
        analysis = analyze_source(source, filename)
    except SyntaxError:
        return []

    findings = []

    # Process each classified function
    for func in analysis.get("functions", []):
        name = func.get("name", "?")
        mother_type = func.get("mother_type", CONTRACT)
        subtype = func.get("subtype", "unknown_subtype")
        is_pure = func.get("is_pure", True)
        lineno = func.get("lineno", 0)
        impurity = func.get("impurity_signals", [])

        # Source anchor
        where = {
            "file": filename,
            "line": lineno,
            "function": name,
        }

        if not is_pure and impurity:
            signal_names = [s.get("call", s.get("attr", "?")) for s in impurity[:3]]
            findings.append({
                "text": f"{name} has impure operations: {', '.join(signal_names)}",
                "mother_type": CONSTRAINT,
                "subtype": "impurity_boundary",
                "confidence": 0.9,
                "claim_type": "constraint",
                "source_span": (lineno, lineno),
                "clause_id": f"fn_{name}",
                "_finding_kind": "impurity",
                "_where": where,
            })
        elif is_pure:
            findings.append({
                "text": f"{name} is pure — no I/O, no side effects",
                "mother_type": WITNESS,
                "subtype": "ast_evidence",
                "confidence": 0.8,
                "claim_type": "guarantee",
                "source_span": (lineno, lineno),
                "clause_id": f"fn_{name}",
                "_finding_kind": "purity",
                "_where": where,
            })

    # Process violations
    for v in analysis.get("violations", []):
        vtype = v.get("type", "unknown")
        message = v.get("message", "")
        func_name = v.get("function", "module")
        lineno = v.get("line", 0)
        vmother = v.get("mother_type", CONSTRAINT)

        where = {
            "file": filename,
            "line": lineno,
            "function": func_name,
        }

        findings.append({
            "text": f"{func_name}:{lineno} — {message}",
            "mother_type": vmother,
            "subtype": vtype,
            "confidence": 0.95,
            "claim_type": "constraint" if vmother == CONSTRAINT else "fact",
            "source_span": (lineno, lineno),
            "clause_id": f"viol_{func_name}_{lineno}",
            "_finding_kind": "violation",
            "_where": where,
        })

    # Process dependency provenance gaps
    for dep in analysis.get("dependencies", []):
        if dep.get("is_external") and not dep.get("is_stdlib"):
            module = dep.get("module", "?")
            findings.append({
                "text": f"External dependency '{module}' has no provenance receipt",
                "mother_type": WITNESS,
                "subtype": "provenance_gap",
                "confidence": 0.7,
                "claim_type": "guarantee",
                "clause_id": f"dep_{module}",
                "_finding_kind": "provenance_gap",
                "_where": {"dependency": module},
            })

    return findings


# ---------------------------------------------------------------------------
# Diff perception (minimal v1)
# ---------------------------------------------------------------------------

def diff_to_findings(diff_text: str) -> list[dict[str, Any]]:
    """Extract basic structural signals from a diff. Pure.

    Minimal v1: detect which files changed, added/removed line counts,
    and flag obvious structural signals.
    """
    findings = []
    current_file = None
    added = 0
    removed = 0

    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            if current_file and (added + removed) > 0:
                findings.append(_diff_file_finding(current_file, added, removed))
            match = re.search(r"b/(.+)$", line)
            current_file = match.group(1) if match else "unknown"
            added = 0
            removed = 0
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1

    if current_file and (added + removed) > 0:
        findings.append(_diff_file_finding(current_file, added, removed))

    return findings


def _diff_file_finding(filename: str, added: int, removed: int) -> dict[str, Any]:
    """Create a finding for a changed file in a diff."""
    total = added + removed
    if removed > added * 2:
        judgment = f"{filename}: significant removal ({removed} lines removed, {added} added)"
        mother_type = CONSTRAINT
        finding_kind = "significant_removal"
    elif added > removed * 3:
        judgment = f"{filename}: significant addition ({added} lines added)"
        mother_type = UNCERTAINTY
        finding_kind = "large_addition"
    else:
        judgment = f"{filename}: modified (+{added}/-{removed})"
        mother_type = RELATION
        finding_kind = "file_change"

    return {
        "text": judgment,
        "mother_type": mother_type,
        "subtype": finding_kind,
        "confidence": 0.6,
        "claim_type": "observation",
        "clause_id": f"diff_{filename}",
        "_finding_kind": finding_kind,
        "_where": {"file": filename, "added": added, "removed": removed},
    }
