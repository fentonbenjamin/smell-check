"""Diff lane tests — guard removal, error path, test gap."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from smell_check.gateway import smell_check
from smell_check.code_perception import diff_to_findings, detect_input_kind


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GUARD_REMOVED_DIFF = """diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -10,10 +10,6 @@
 def process_request(request):
-    if not request.user.is_authenticated:
-        raise PermissionError("Not authenticated")
-    if not request.user.has_permission("admin"):
-        raise PermissionError("Not authorized")
     data = request.get_json()
     return handle(data)
"""

ERROR_PATH_DIFF = """diff --git a/handler.py b/handler.py
--- a/handler.py
+++ b/handler.py
@@ -5,8 +5,4 @@
 def handle(data):
-    try:
-        result = process(data)
-    except ValueError:
-        logging.error("Bad data")
-        return None
+    result = process(data)
     return result
"""

TEST_GAP_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,5 +1,10 @@
 def load_config(path):
-    return {}
+    import json
+    with open(path) as f:
+        return json.load(f)
+
+def validate(cfg):
+    if not isinstance(cfg, dict):
+        raise ValueError("bad config")
"""

WITH_TEST_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,5 @@
 def load_config(path):
-    return {}
+    import json
+    with open(path) as f:
+        return json.load(f)
diff --git a/tests/test_app.py b/tests/test_app.py
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1,3 +1,6 @@
+def test_load_config():
+    result = load_config("test.json")
+    assert isinstance(result, dict)
"""

CLEAN_DIFF = """diff --git a/utils.py b/utils.py
--- a/utils.py
+++ b/utils.py
@@ -1,3 +1,3 @@
 def format_name(first, last):
-    return first + " " + last
+    return f"{first} {last}"
"""


# ---------------------------------------------------------------------------
# Guard removal detection
# ---------------------------------------------------------------------------

class TestGuardRemoval:

    def test_detects_removed_guards(self):
        findings = diff_to_findings(GUARD_REMOVED_DIFF)
        guard_findings = [f for f in findings if f["_finding_kind"] == "guard_removed"]
        assert len(guard_findings) >= 1, f"No guard_removed findings: {[f['_finding_kind'] for f in findings]}"

    def test_guard_removal_in_smell_check(self):
        result = smell_check(GUARD_REMOVED_DIFF)
        finding_texts = [f["judgment"].lower() for f in result["findings"]]
        assert any("guard" in t for t in finding_texts), (
            f"Guard removal not in findings: {finding_texts}"
        )

    def test_clean_diff_no_guard_finding(self):
        findings = diff_to_findings(CLEAN_DIFF)
        guard_findings = [f for f in findings if f["_finding_kind"] == "guard_removed"]
        assert len(guard_findings) == 0


# ---------------------------------------------------------------------------
# Error path changes
# ---------------------------------------------------------------------------

class TestErrorPathChanged:

    def test_detects_error_path_change(self):
        findings = diff_to_findings(ERROR_PATH_DIFF)
        error_findings = [f for f in findings if f["_finding_kind"] == "error_path_changed"]
        assert len(error_findings) >= 1

    def test_error_path_in_smell_check(self):
        result = smell_check(ERROR_PATH_DIFF)
        finding_texts = [f["judgment"].lower() for f in result["findings"]]
        assert any("error" in t for t in finding_texts), (
            f"Error path not in findings: {finding_texts}"
        )


# ---------------------------------------------------------------------------
# Test gap detection
# ---------------------------------------------------------------------------

class TestTestGap:

    def test_detects_test_gap(self):
        findings = diff_to_findings(TEST_GAP_DIFF)
        gap_findings = [f for f in findings if f["_finding_kind"] == "test_gap"]
        assert len(gap_findings) >= 1, f"No test_gap: {[f['_finding_kind'] for f in findings]}"

    def test_no_gap_when_tests_present(self):
        findings = diff_to_findings(WITH_TEST_DIFF)
        gap_findings = [f for f in findings if f["_finding_kind"] == "test_gap"]
        assert len(gap_findings) == 0, f"False test_gap when tests present: {gap_findings}"

    def test_test_gap_in_smell_check(self):
        result = smell_check(TEST_GAP_DIFF)
        finding_texts = [f["judgment"].lower() for f in result["findings"]]
        assert any("test" in t or "no test" in t for t in finding_texts), (
            f"Test gap not in findings: {finding_texts}"
        )


# ---------------------------------------------------------------------------
# Input detection
# ---------------------------------------------------------------------------

class TestDiffDetection:

    def test_detects_diff_input(self):
        kind = detect_input_kind(GUARD_REMOVED_DIFF)
        assert kind in ("diff", "mixed"), f"Got {kind}"

    def test_clean_diff_detects(self):
        kind = detect_input_kind(CLEAN_DIFF)
        assert kind in ("diff", "mixed"), f"Got {kind}"


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------

class TestCombinedDiff:

    def test_receipt_valid_on_diff(self):
        result = smell_check(GUARD_REMOVED_DIFF)
        assert result["receipt_status"]["valid"] is True

    def test_multiple_signals_in_one_diff(self):
        """A diff with guard removal + error path change should produce both."""
        combined = GUARD_REMOVED_DIFF + ERROR_PATH_DIFF
        findings = diff_to_findings(combined)
        kinds = {f["_finding_kind"] for f in findings}
        assert "guard_removed" in kinds, f"Missing guard_removed: {kinds}"
        assert "error_path_changed" in kinds, f"Missing error_path_changed: {kinds}"
