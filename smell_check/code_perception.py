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

    Mixed: input contains both code blocks and prose (e.g., PR review
    with inline code, commit message + diff, review comments + snippets).
    """
    lines = text.strip().split("\n")
    if not lines:
        return "thread"

    # Diff detection
    diff_signals = sum(1 for l in lines[:20] if l.startswith(("diff --git", "+++", "---", "@@")))
    if diff_signals > len(lines[:20]) * 0.3:
        # Check if there's also prose around the diff
        prose_signals = sum(1 for l in lines if l.strip() and not l.startswith((
            "diff ", "+++", "---", "@@", "+", "-", "index ", "Binary "
        )) and any(c.isalpha() for c in l))
        if prose_signals > 3:
            return "mixed"
        return "diff"

    # Python source detection
    code_signals = 0
    for line in lines[:50]:
        stripped = line.strip()
        if stripped.startswith(("def ", "class ", "import ", "from ", "if __name__")):
            code_signals += 3
        elif stripped.startswith(("#", "'''", '"""')):
            code_signals += 1
        elif re.match(r"^\s+(return |yield |raise |pass$|self\.)", stripped):
            code_signals += 2
        elif re.match(r"^\s*\w+\s*=\s*", stripped):
            code_signals += 1

    # Thread/conversation signals — but NOT from lines that look like
    # code comments, docstrings, or indented code context
    thread_signals = 0
    thread_cues = ("we decided", "not sure", "someone needs", "said", "agreed",
                   "i think", "looks good", "lgtm", "nit:", "should we",
                   "needs review", "approve", "changes requested")
    in_docstring = False
    for l in lines:
        stripped = l.strip()
        # Track docstring boundaries
        if '"""' in stripped or "'''" in stripped:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        # Skip comments and indented code
        if stripped.startswith("#") or stripped.startswith(("def ", "class ", "import ", "from ")):
            continue
        if l.startswith(("    ", "\t")):
            continue
        if any(cue in l.lower() for cue in thread_cues):
            thread_signals += 1

    # Mixed: both code and thread signals present
    if code_signals > 5 and thread_signals >= 2:
        return "mixed"

    if code_signals > len(lines[:50]) * 0.3:
        return "python_source"

    if thread_signals > 0:
        return "thread"

    if code_signals > 3:
        return "python_source"

    return "thread"


def split_mixed_input(text: str) -> dict[str, list[str]]:
    """Split mixed input into code segments and prose segments. Pure.

    Returns {"code": [...], "prose": [...]} where each list contains
    text segments of that kind.
    """
    lines = text.split("\n")
    segments: dict[str, list[str]] = {"code": [], "prose": []}
    current_kind = "prose"
    current_lines: list[str] = []

    # Fenced code block detection
    in_fence = False

    for line in lines:
        stripped = line.strip()

        # Fenced code blocks
        if stripped.startswith("```"):
            if in_fence:
                # End of code block
                current_lines.append(line)
                segments["code"].append("\n".join(current_lines))
                current_lines = []
                in_fence = False
                current_kind = "prose"
                continue
            else:
                # Start of code block
                if current_lines:
                    segments[current_kind].append("\n".join(current_lines))
                current_lines = [line]
                in_fence = True
                current_kind = "code"
                continue

        if in_fence:
            current_lines.append(line)
            continue

        # Heuristic: is this line code or prose?
        is_code_line = (
            stripped.startswith(("def ", "class ", "import ", "from ", "if ", "else:", "elif ",
                                "for ", "while ", "return ", "yield ", "raise ", "try:", "except",
                                "with ", "    ", "\t"))
            or stripped.startswith(("@", ">>>"))
            or (stripped.startswith(("diff --git", "+++", "---", "@@", "+", "-")) and not stripped.startswith("- "))
        )

        new_kind = "code" if is_code_line else "prose"

        if new_kind != current_kind and current_lines:
            segments[current_kind].append("\n".join(current_lines))
            current_lines = []

        current_kind = new_kind
        current_lines.append(line)

    if current_lines:
        segments[current_kind].append("\n".join(current_lines))

    return segments


# ---------------------------------------------------------------------------
# Analyzer → typed findings adapter
# ---------------------------------------------------------------------------

def _extract_local_modules(source: str) -> set[str]:
    """Extract module names that are likely local/in-repo from relative imports. Pure.

    Relative imports (from .foo, from ..bar) are always local.
    We also infer top-level modules from the source's own package structure.
    """
    import ast as _ast
    local = set()
    try:
        tree = _ast.parse(source)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom):
                if node.level and node.level > 0:
                    # Relative import — always local
                    if node.module:
                        local.add(node.module.split(".")[0])
                    # Also mark the imported names as local origins
                    for alias in (node.names or []):
                        if alias.name != "*":
                            local.add(alias.name.split(".")[0])
    except SyntaxError:
        pass
    return local


def analyzer_to_findings(source: str, filename: str = "<input>") -> list[dict[str, Any]]:
    """Convert analyzer output to typed findings for the sieve. Pure.

    Each finding becomes a claim-shaped dict that the sieve can consume,
    with mother_type, text, and structural drillback.
    """
    # Build a local module map from relative imports in the source
    local_modules = _extract_local_modules(source)

    try:
        analysis = analyze_source(source, filename, repo_modules=local_modules)
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
            signal_lines = [s.get("line", lineno) for s in impurity[:3]]
            findings.append({
                "text": f"{name} has impure operations: {', '.join(signal_names)}",
                "mother_type": CONSTRAINT,
                "subtype": "impurity_boundary",
                "confidence": 0.9,
                "claim_type": "constraint",
                "source_span": (lineno, lineno),
                "clause_id": f"fn_{name}",
                "_finding_kind": "impurity",
                "_evidence_basis": "ast",
                "_signals": signal_names,
                "_signal_lines": signal_lines,
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
                "_evidence_basis": "ast",
                "_where": where,
            })

    # Process exception safety signals
    for func in analysis.get("functions", []):
        name = func.get("name", "?")
        for sig in func.get("exception_signals", []):
            sig_line = sig.get("line", func.get("lineno", 0))
            where = {"file": filename, "line": sig_line, "function": name}
            findings.append({
                "text": f"{name}:{sig_line} — {sig['message']}",
                "mother_type": CONSTRAINT,
                "subtype": sig["type"],
                "confidence": 0.85,
                "claim_type": "constraint",
                "source_span": (sig_line, sig_line),
                "clause_id": f"exc_{name}_{sig_line}",
                "_finding_kind": "exception_safety",
                "_evidence_basis": "ast",
                "_where": where,
            })

    # Process guard detection — guards are stable points (validation exists)
    for func in analysis.get("functions", []):
        name = func.get("name", "?")
        guards = func.get("guards", [])
        if guards:
            guard_lines = [g["line"] for g in guards]
            where = {"file": filename, "line": guards[0]["line"], "function": name}
            findings.append({
                "text": f"{name} has {len(guards)} validation guard{'s' if len(guards) != 1 else ''}",
                "mother_type": WITNESS,
                "subtype": "validation_guard",
                "confidence": 0.75,
                "claim_type": "guarantee",
                "source_span": (guards[0]["line"], guards[-1]["line"]),
                "clause_id": f"guard_{name}",
                "_finding_kind": "guard_present",
                "_evidence_basis": "ast",
                "_where": where,
            })

    # Process global state mutations
    for func in analysis.get("functions", []):
        name = func.get("name", "?")
        mutations = func.get("global_mutations", [])
        for mut in mutations:
            mut_line = mut.get("line", func.get("lineno", 0))
            where = {"file": filename, "line": mut_line, "function": name}
            findings.append({
                "text": f"{name} mutates global state: {mut['name']}",
                "mother_type": CONSTRAINT,
                "subtype": "global_mutation",
                "confidence": 0.9,
                "claim_type": "constraint",
                "source_span": (mut_line, mut_line),
                "clause_id": f"global_{name}_{mut['name']}",
                "_finding_kind": "global_mutation",
                "_evidence_basis": "ast",
                "_where": where,
            })

    # Process violations — only those with structural (AST) basis
    for v in analysis.get("violations", []):
        vtype = v.get("type", "unknown")
        message = v.get("message", "")
        func_name = v.get("function", "module")
        lineno = v.get("line", 0)
        vmother = v.get("mother_type", CONSTRAINT)

        # Skip violations that are just contract-inference heuristics
        # without a concrete AST signal backing them
        if vtype == "contract_mismatch" and not v.get("impurity_signals"):
            # Contract mismatch from name-inference only — downrank
            confidence = 0.5
        else:
            confidence = 0.95

        where = {
            "file": filename,
            "line": lineno,
            "function": func_name,
        }

        findings.append({
            "text": f"{func_name}:{lineno} — {message}",
            "mother_type": vmother,
            "subtype": vtype,
            "confidence": confidence,
            "claim_type": "constraint" if vmother == CONSTRAINT else "fact",
            "source_span": (lineno, lineno),
            "clause_id": f"viol_{func_name}_{lineno}",
            "_finding_kind": "violation",
            "_evidence_basis": "ast",
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
# Diff perception
# ---------------------------------------------------------------------------

# Structural patterns to detect in removed/added lines
_GUARD_PATTERNS = re.compile(
    r"^\s*if\s+.+:\s*$"
    r"|^\s*raise\s+"
    r"|^\s*assert\s+"
    r"|^\s*if\s+not\s+"
, re.MULTILINE)

_ERROR_HANDLING_PATTERNS = re.compile(
    r"^\s*try\s*:"
    r"|^\s*except\s+"
    r"|^\s*finally\s*:"
    r"|^\s*raise\s+"
    r"|^\s*logging\.\w+\("
    r"|^\s*logger\.\w+\("
, re.MULTILINE)

_TEST_FILE_PATTERNS = re.compile(r"test_|_test\.py|tests/|spec_|_spec\.py")


def diff_to_findings(diff_text: str) -> list[dict[str, Any]]:
    """Extract structural signals from a diff. Pure.

    Detects:
    - guard_removed: if/raise/assert removed without replacement
    - error_path_changed: try/except/raise/logging changed
    - test_gap: behavior changed but no test file in the diff
    - significant_removal: large net deletion
    - large_addition: large net addition
    - file_change: basic file modification
    """
    findings = []
    files = _parse_diff_files(diff_text)

    has_test_file = any(_TEST_FILE_PATTERNS.search(f["filename"]) for f in files)
    has_behavior_change = False

    for f in files:
        filename = f["filename"]
        added_lines = f["added_lines"]
        removed_lines = f["removed_lines"]
        added_count = len(added_lines)
        removed_count = len(removed_lines)

        where = {"file": filename, "added": added_count, "removed": removed_count}

        # Guard removal: if/raise/assert removed without corresponding addition
        removed_guards = [l for l in removed_lines if _GUARD_PATTERNS.search(l)]
        added_guards = [l for l in added_lines if _GUARD_PATTERNS.search(l)]
        if removed_guards and len(removed_guards) > len(added_guards):
            net_removed = len(removed_guards) - len(added_guards)
            findings.append({
                "text": f"{filename}: {net_removed} guard/validation line{'s' if net_removed != 1 else ''} removed",
                "mother_type": CONSTRAINT,
                "subtype": "guard_removed",
                "confidence": 0.85,
                "claim_type": "constraint",
                "clause_id": f"diff_guard_{filename}",
                "_finding_kind": "guard_removed",
                "_evidence_basis": "diff",
                "_where": where,
                "_removed_guards": [l.strip() for l in removed_guards[:3]],
            })

        # Error path changed: try/except/raise/logging touched
        removed_error = [l for l in removed_lines if _ERROR_HANDLING_PATTERNS.search(l)]
        added_error = [l for l in added_lines if _ERROR_HANDLING_PATTERNS.search(l)]
        if removed_error or added_error:
            findings.append({
                "text": f"{filename}: error handling changed (-{len(removed_error)}/+{len(added_error)} error-path lines)",
                "mother_type": UNCERTAINTY,
                "subtype": "error_path_changed",
                "confidence": 0.7,
                "claim_type": "observation",
                "clause_id": f"diff_error_{filename}",
                "_finding_kind": "error_path_changed",
                "_evidence_basis": "diff",
                "_where": where,
            })

        # Track whether this is a behavior change (not just a test or doc)
        if not _TEST_FILE_PATTERNS.search(filename) and added_count + removed_count > 2:
            has_behavior_change = True

        # Size-based signals
        if removed_count > added_count * 2 and removed_count > 5:
            findings.append({
                "text": f"{filename}: significant removal ({removed_count} lines removed, {added_count} added)",
                "mother_type": CONSTRAINT,
                "subtype": "significant_removal",
                "confidence": 0.6,
                "claim_type": "observation",
                "clause_id": f"diff_size_{filename}",
                "_finding_kind": "significant_removal",
                "_evidence_basis": "diff",
                "_where": where,
            })
        elif added_count > removed_count * 3 and added_count > 10:
            findings.append({
                "text": f"{filename}: large addition ({added_count} lines added)",
                "mother_type": UNCERTAINTY,
                "subtype": "large_addition",
                "confidence": 0.5,
                "claim_type": "observation",
                "clause_id": f"diff_size_{filename}",
                "_finding_kind": "large_addition",
                "_evidence_basis": "diff",
                "_where": where,
            })

    # Test gap: behavior changed but no test file touched
    if has_behavior_change and not has_test_file:
        behavior_files = [f["filename"] for f in files
                         if not _TEST_FILE_PATTERNS.search(f["filename"])
                         and len(f["added_lines"]) + len(f["removed_lines"]) > 2]
        if behavior_files:
            findings.append({
                "text": f"Behavior changed in {', '.join(behavior_files[:3])} but no test file in this diff",
                "mother_type": UNCERTAINTY,
                "subtype": "test_gap",
                "confidence": 0.75,
                "claim_type": "observation",
                "clause_id": "diff_test_gap",
                "_finding_kind": "test_gap",
                "_evidence_basis": "diff",
                "_where": {"files": behavior_files},
            })

    return findings


def _parse_diff_files(diff_text: str) -> list[dict[str, Any]]:
    """Parse a unified diff into per-file structures. Pure."""
    files = []
    current_file = None
    added_lines: list[str] = []
    removed_lines: list[str] = []

    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            if current_file:
                files.append({
                    "filename": current_file,
                    "added_lines": added_lines,
                    "removed_lines": removed_lines,
                })
            match = re.search(r"b/(.+)$", line)
            current_file = match.group(1) if match else "unknown"
            added_lines = []
            removed_lines = []
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])  # strip the leading +
        elif line.startswith("-") and not line.startswith("---"):
            removed_lines.append(line[1:])  # strip the leading -

    if current_file:
        files.append({
            "filename": current_file,
            "added_lines": added_lines,
            "removed_lines": removed_lines,
        })

    return files


def _diff_file_finding(filename: str, added: int, removed: int) -> dict[str, Any]:
    """Create a finding for a changed file in a diff. Legacy helper."""
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
