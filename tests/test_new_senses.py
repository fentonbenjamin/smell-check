"""Tests for new code senses: exception safety, guards, global state."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from smell_check.gateway import smell_check
from smell_check.analyzer import extract_functions


# ---------------------------------------------------------------------------
# Exception safety
# ---------------------------------------------------------------------------

EXCEPTION_CODE = '''
import json

def safe_parse(data):
    """Parse JSON with error handling."""
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None

def risky_parse(data):
    """Parse JSON without error handling."""
    return json.loads(data)

def silent_fail(data):
    """Silently swallows all errors."""
    try:
        return json.loads(data)
    except Exception:
        pass

def bare_except_func(data):
    """Uses bare except."""
    try:
        return int(data)
    except:
        return 0
'''


class TestExceptionSafety:

    def test_detects_bare_except(self):
        funcs = extract_functions(EXCEPTION_CODE)
        bare = next(f for f in funcs if f["name"] == "bare_except_func")
        signals = bare.get("exception_signals", [])
        assert any(s["type"] == "bare_except" for s in signals), (
            f"Bare except not detected: {signals}"
        )

    def test_detects_silent_exception(self):
        funcs = extract_functions(EXCEPTION_CODE)
        silent = next(f for f in funcs if f["name"] == "silent_fail")
        signals = silent.get("exception_signals", [])
        assert any(s["type"] == "silent_exception" for s in signals), (
            f"Silent exception not detected: {signals}"
        )

    def test_detects_unguarded_deserialize(self):
        funcs = extract_functions(EXCEPTION_CODE)
        risky = next(f for f in funcs if f["name"] == "risky_parse")
        signals = risky.get("exception_signals", [])
        assert any(s["type"] == "unguarded_deserialize" for s in signals), (
            f"Unguarded deserialize not detected: {signals}"
        )

    def test_safe_parse_no_signals(self):
        funcs = extract_functions(EXCEPTION_CODE)
        safe = next(f for f in funcs if f["name"] == "safe_parse")
        signals = safe.get("exception_signals", [])
        assert len(signals) == 0, f"False positive on safe_parse: {signals}"

    def test_exception_findings_in_smell_check(self):
        result = smell_check(EXCEPTION_CODE)
        finding_texts = [f["judgment"].lower() for f in result["findings"]]
        assert any("bare except" in t or "silent" in t or "unguarded" in t
                    for t in finding_texts), (
            f"No exception findings: {finding_texts}"
        )


# ---------------------------------------------------------------------------
# Guard detection
# ---------------------------------------------------------------------------

GUARD_CODE = '''
def validate_input(data):
    """Validate input with guard."""
    if not isinstance(data, dict):
        raise ValueError("Expected dict")
    if "name" not in data:
        raise KeyError("Missing name")
    return data

def no_guard(data):
    """No validation at all."""
    return data["name"]

def partial_guard(data):
    """Checks type but not keys."""
    if not isinstance(data, dict):
        raise TypeError("Not a dict")
    return data["name"]
'''


class TestGuardDetection:

    def test_detects_guards(self):
        funcs = extract_functions(GUARD_CODE)
        validated = next(f for f in funcs if f["name"] == "validate_input")
        guards = validated.get("guards", [])
        assert len(guards) == 2, f"Expected 2 guards, got {len(guards)}: {guards}"

    def test_no_guard_detected(self):
        funcs = extract_functions(GUARD_CODE)
        unguarded = next(f for f in funcs if f["name"] == "no_guard")
        guards = unguarded.get("guards", [])
        assert len(guards) == 0

    def test_guards_in_stable_points(self):
        result = smell_check(GUARD_CODE)
        stable_texts = [s["judgment"].lower() for s in result["stable_points"]]
        assert any("guard" in t or "validation" in t for t in stable_texts), (
            f"Guards not in stable points: {stable_texts}"
        )


# ---------------------------------------------------------------------------
# Global state mutation
# ---------------------------------------------------------------------------

GLOBAL_CODE = '''
_counter = 0
_cache = {}

def increment():
    """Mutates global counter."""
    global _counter
    _counter += 1
    return _counter

def update_cache(key, value):
    """Mutates global cache."""
    global _cache
    _cache[key] = value

def read_only():
    """Pure function, no globals."""
    return 42
'''


IMPLICIT_GLOBAL_CODE = '''
CACHE = {}
ITEMS = []
REGISTRY = set()

def remember(key, value):
    CACHE[key] = value

def add_item(x):
    ITEMS.append(x)

def register(name):
    REGISTRY.add(name)

def process(data, local_dict):
    """Should NOT be flagged — mutates parameter, not module-level."""
    local_dict["key"] = "value"
    return data
'''


class TestGlobalState:

    def test_detects_explicit_global_mutation(self):
        funcs = extract_functions(GLOBAL_CODE)
        inc = next(f for f in funcs if f["name"] == "increment")
        mutations = inc.get("global_mutations", [])
        assert any(m["name"] == "_counter" for m in mutations), (
            f"Global mutation not detected: {mutations}"
        )

    def test_detects_implicit_dict_mutation(self):
        funcs = extract_functions(IMPLICIT_GLOBAL_CODE)
        remember = next(f for f in funcs if f["name"] == "remember")
        mutations = remember.get("global_mutations", [])
        assert any(m["name"] == "CACHE" for m in mutations), (
            f"Implicit dict mutation not detected: {mutations}"
        )

    def test_detects_implicit_list_mutation(self):
        funcs = extract_functions(IMPLICIT_GLOBAL_CODE)
        add = next(f for f in funcs if f["name"] == "add_item")
        mutations = add.get("global_mutations", [])
        assert any(m["name"] == "ITEMS" for m in mutations), (
            f"Implicit list mutation not detected: {mutations}"
        )

    def test_detects_implicit_set_mutation(self):
        funcs = extract_functions(IMPLICIT_GLOBAL_CODE)
        reg = next(f for f in funcs if f["name"] == "register")
        mutations = reg.get("global_mutations", [])
        assert any(m["name"] == "REGISTRY" for m in mutations), (
            f"Implicit set mutation not detected: {mutations}"
        )

    def test_no_false_positive_on_parameter(self):
        funcs = extract_functions(IMPLICIT_GLOBAL_CODE)
        proc = next(f for f in funcs if f["name"] == "process")
        mutations = proc.get("global_mutations", [])
        assert len(mutations) == 0, (
            f"False positive on parameter mutation: {mutations}"
        )

    def test_no_global_for_pure(self):
        funcs = extract_functions(GLOBAL_CODE)
        pure = next(f for f in funcs if f["name"] == "read_only")
        mutations = pure.get("global_mutations", [])
        assert len(mutations) == 0

    def test_global_mutation_in_findings(self):
        result = smell_check(GLOBAL_CODE)
        finding_texts = [f["judgment"].lower() for f in result["findings"]]
        assert any("global" in t or "mutates" in t for t in finding_texts), (
            f"Global mutation not in findings: {finding_texts}"
        )


# ---------------------------------------------------------------------------
# Combined: all new senses through smell_check
# ---------------------------------------------------------------------------

COMBINED_CODE = '''
import json

_state = {}

def safe_handler(data):
    """Well-guarded function."""
    if not isinstance(data, str):
        raise TypeError("Expected string")
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None

def risky_handler(data):
    """Multiple issues."""
    global _state
    result = json.loads(data)
    _state[data] = result
    return result

def swallower(x):
    try:
        return int(x)
    except:
        pass
'''


class TestCombinedSenses:

    def test_combined_output(self):
        result = smell_check(COMBINED_CODE)
        findings = result["findings"]
        stable = result["stable_points"]

        # safe_handler should be stable (has guard + try/except)
        stable_names = " ".join(s["judgment"] for s in stable)
        assert "safe_handler" in stable_names or len(stable) > 0

        # risky_handler should have findings (global + unguarded)
        finding_names = " ".join(f["judgment"] for f in findings)
        assert "risky_handler" in finding_names or "global" in finding_names.lower() or "unguarded" in finding_names.lower()

        # swallower should have bare_except finding
        assert any("bare except" in f["judgment"].lower() or "swallower" in f["judgment"]
                    for f in findings), (
            f"Swallower not flagged: {[f['judgment'] for f in findings]}"
        )

    def test_receipt_still_valid(self):
        result = smell_check(COMBINED_CODE)
        assert result["receipt_status"]["valid"] is True
        assert result["receipt_status"]["wall"] == "held"
