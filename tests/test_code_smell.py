"""Code smell regression tests.

Fixtures from real bad output: prose cues firing on code,
purity findings contested, local imports flagged as external,
security heuristics overfiring on comments.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from smell_check.gateway import smell_check
from smell_check.code_perception import detect_input_kind, analyzer_to_findings


# A realistic Python source file with docstrings, comments,
# relative imports, and mixed pure/impure functions.
SAMPLE_CODE = '''
"""Outline module — need to handle crosslinks carefully.

Should we refactor the section extraction? What about
skipping empty headers? This is a TODO for later.
"""

from __future__ import annotations

import re
from typing import Any

from .models import Artifact
from .kernel import StoreProtocol
from .hashing import hash_content


def compute_outline_delta(old: list, new: list) -> dict:
    """Compute the diff between two outlines. Pure."""
    added = [s for s in new if s not in old]
    removed = [s for s in old if s not in new]
    return {"added": added, "removed": removed}


def extract_sections(text: str) -> list[str]:
    """Extract markdown sections from text. Pure."""
    return re.findall(r"^#+\\s+(.+)$", text, re.MULTILINE)


def save_outline(store: StoreProtocol, outline: dict) -> None:
    """Save outline to store. Impure — writes to storage."""
    blob = hash_content(str(outline).encode())
    store.write_json(f"outlines/{blob}.json", outline)


def load_config(path: str) -> dict:
    """Load configuration from disk."""
    import json
    with open(path) as f:
        return json.load(f)
'''


class TestCodeInputRouting:

    def test_detects_python_source(self):
        assert detect_input_kind(SAMPLE_CODE) == "python_source"

    def test_code_does_not_produce_thread_cues(self):
        """Docstring prose like 'need to', 'should we', 'what about'
        must NOT produce thread-lane findings."""
        result = smell_check(SAMPLE_CODE)
        for f in result["findings"]:
            judgment = f["judgment"].lower()
            # Thread-lane cue language should not appear
            assert "someone needs to act" not in judgment, (
                f"Thread cue fired on code: {f['judgment']}"
            )
            assert "action item" not in judgment or "action_required" not in f.get("drillback", {}).get("epistemic_event", ""), (
                f"Thread action cue fired on code: {f['judgment']}"
            )

    def test_no_unclear_from_docstring_questions(self):
        """'What about skipping empty headers?' in a docstring
        should not produce an 'unclear' open question."""
        result = smell_check(SAMPLE_CODE)
        for q in result["open_questions"]:
            assert "what about" not in q["judgment"].lower(), (
                f"Docstring question treated as uncertainty: {q['judgment']}"
            )


class TestPurityFindings:

    def test_pure_functions_are_stable(self):
        result = smell_check(SAMPLE_CODE)
        stable_texts = [s["judgment"].lower() for s in result["stable_points"]]
        # compute_outline_delta and extract_sections should be stable
        assert any("compute_outline_delta" in t or "structurally pure" in t for t in stable_texts), (
            f"Pure function not in stable: {stable_texts}"
        )

    def test_pure_functions_not_contested(self):
        """Pure functions should not appear in findings as 'smells funny'."""
        result = smell_check(SAMPLE_CODE)
        for f in result["findings"]:
            judgment = f["judgment"].lower()
            # Pure functions should not be contested
            assert "compute_outline_delta" not in judgment or "pure" not in judgment, (
                f"Pure function incorrectly contested: {f['judgment']}"
            )

    def test_impure_functions_are_findings(self):
        result = smell_check(SAMPLE_CODE)
        finding_texts = [f["judgment"].lower() for f in result["findings"]]
        # save_outline and load_config are impure — should be findings
        assert any("save_outline" in t or "load_config" in t for t in finding_texts), (
            f"Impure functions not flagged: {finding_texts}"
        )


class TestLocalImports:

    def test_relative_imports_not_flagged_as_external(self):
        """from .models, from .kernel, from .hashing are local.
        They should NOT be flagged as unstamped external deps."""
        result = smell_check(SAMPLE_CODE)
        for f in result["findings"]:
            judgment = f["judgment"].lower()
            for local_mod in ["models", "kernel", "hashing"]:
                assert f"'{local_mod}'" not in judgment or "external" not in judgment, (
                    f"Local import flagged as external: {f['judgment']}"
                )

    def test_local_modules_resolved(self):
        """The code perception lane should recognize relative imports as local."""
        findings = analyzer_to_findings(SAMPLE_CODE)
        provenance_gaps = [f for f in findings if f.get("_finding_kind") == "provenance_gap"]
        gap_modules = [f["text"] for f in provenance_gaps]
        for mod in ["models", "kernel", "hashing"]:
            assert not any(mod in g for g in gap_modules), (
                f"Local module {mod} flagged as provenance gap: {gap_modules}"
            )


class TestSecurityHeuristics:

    def test_no_false_security_findings_from_comments(self):
        """Words like 'skip', 'dangerous', 'verify' in prose/comments
        should not trigger security violations."""
        code_with_comments = '''
# We should skip the verification step for now
# This is not dangerous, just a placeholder
# TODO: verify the auth flow later

def safe_function(x):
    """A perfectly safe function. Nothing dangerous here."""
    return x + 1
'''
        result = smell_check(code_with_comments)
        for f in result["findings"]:
            judgment = f["judgment"].lower()
            assert "dangerous" not in judgment or f.get("drillback", {}).get("subtype") in (
                "dangerous_deserialization", "code_execution", "dangerous_code_enabled"
            ), f"False security finding from comments: {f['judgment']}"


class TestCodeRendering:

    def test_findings_use_code_language(self):
        """Code findings should use structural language, not 'This smells funny'."""
        result = smell_check(SAMPLE_CODE)
        for f in result["findings"]:
            judgment = f["judgment"]
            assert "smells funny" not in judgment.lower(), (
                f"Generic rendering on code finding: {judgment}"
            )

    def test_stable_points_use_structural_language(self):
        """Stable code findings should say 'structurally pure', not 'decided'."""
        result = smell_check(SAMPLE_CODE)
        for s in result["stable_points"]:
            assert "decided" not in s["judgment"].lower(), (
                f"Code stable point rendered as decision: {s['judgment']}"
            )

    def test_receipt_status_in_output(self):
        """Output should include receipt_status section."""
        result = smell_check(SAMPLE_CODE)
        assert "receipt_status" in result
        assert result["receipt_status"]["wall"] == "held"
        assert result["receipt_status"]["valid"] is True

    def test_findings_have_file_line_anchors(self):
        """Code findings should have file/line/function in where."""
        result = smell_check(SAMPLE_CODE)
        for f in result["findings"]:
            where = f.get("where", {})
            if where.get("file"):  # code finding
                assert "function" in where or "line" in where, (
                    f"Code finding missing anchor: {f['judgment']}"
                )
