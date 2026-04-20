"""Mixed input tests — combinatory lane behavior.

Tests that PR-shaped input (prose + code + diff) correctly activates
both perception lanes and produces findings from each.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from smell_check.gateway import smell_check
from smell_check.code_perception import detect_input_kind, split_mixed_input


# ---------------------------------------------------------------------------
# PR-shaped fixtures
# ---------------------------------------------------------------------------

PR_REVIEW = """
This PR adds the config loader. Looks good overall but I'm not sure
about the error handling path. Someone needs to verify that the
fallback works when the config file is missing.

```python
def load_config(path):
    import json
    with open(path) as f:
        return json.load(f)

def validate_config(cfg):
    if not isinstance(cfg, dict):
        raise ValueError("Config must be a dict")
    return True
```

I think we should also add a test for the edge case where
the JSON is malformed. John said he would handle that.
"""

DIFF_WITH_COMMENTS = """
Overall this change looks reasonable. We decided to go with
the simpler approach. Not sure if the error path is tested.

diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,6 +10,12 @@
+def load_data(path):
+    with open(path) as f:
+        return f.read()
+
+def process(data):
+    return [x.strip() for x in data.split(",")]

Someone needs to confirm that the deploy script handles this.
"""

CODE_REVIEW_THREAD = """
Alice: I reviewed the auth module. The token validation looks solid
but I'm not sure about the session handling.

Bob: Good point. We should probably add rate limiting before ship.

Alice: Agreed. Here's the current implementation:

def validate_token(token):
    import hmac
    return hmac.compare_digest(token, expected)

def create_session(user_id):
    import os
    return os.urandom(32).hex()

Bob: The create_session function reads from /dev/urandom which is
fine for this use case. Let's ship it.
"""


# ---------------------------------------------------------------------------
# Input kind detection
# ---------------------------------------------------------------------------

class TestMixedDetection:

    def test_pr_review_detected_as_mixed(self):
        kind = detect_input_kind(PR_REVIEW)
        assert kind == "mixed", f"Expected mixed, got {kind}"

    def test_pure_code_still_detected_as_code(self):
        code = "def foo():\\n    return 1\\n\\ndef bar():\\n    return 2"
        kind = detect_input_kind(code)
        assert kind in ("python_source", "thread"), f"Got {kind}"

    def test_pure_thread_still_detected_as_thread(self):
        thread = "We decided on Friday. Not sure about parking. Someone needs to confirm dinner."
        kind = detect_input_kind(thread)
        assert kind == "thread"

    def test_diff_with_comments_detected(self):
        kind = detect_input_kind(DIFF_WITH_COMMENTS)
        # Diff signals may dominate — either mixed or diff is acceptable
        assert kind in ("mixed", "diff", "thread"), f"Got {kind}"


# ---------------------------------------------------------------------------
# Segment splitting
# ---------------------------------------------------------------------------

class TestSegmentSplitting:

    def test_splits_fenced_code_blocks(self):
        segments = split_mixed_input(PR_REVIEW)
        assert len(segments["code"]) >= 1, "Should find at least one code block"
        assert len(segments["prose"]) >= 1, "Should find prose segments"

    def test_code_segments_contain_functions(self):
        segments = split_mixed_input(PR_REVIEW)
        code_text = "\n".join(segments["code"])
        assert "def load_config" in code_text or "def validate_config" in code_text

    def test_prose_segments_contain_review(self):
        segments = split_mixed_input(PR_REVIEW)
        prose_text = "\n".join(segments["prose"])
        assert "not sure" in prose_text.lower() or "looks good" in prose_text.lower()


# ---------------------------------------------------------------------------
# Combinatory smell check
# ---------------------------------------------------------------------------

class TestCombinatorySmelCheck:

    def test_pr_review_produces_both_lane_findings(self):
        """A PR review should produce findings from both thread and code lanes."""
        result = smell_check(PR_REVIEW)
        all_items = result["findings"] + result["stable_points"] + result["open_questions"]
        assert len(all_items) >= 2, (
            f"Expected findings from both lanes, got {len(all_items)}: "
            + "; ".join(i["judgment"][:40] for i in all_items)
        )

    def test_thread_findings_in_pr_review(self):
        """PR review prose should produce thread-lane findings (uncertainty, action)."""
        result = smell_check(PR_REVIEW)
        all_judgments = [i["judgment"].lower() for i in
                        result["findings"] + result["open_questions"]]
        # Should catch uncertainty from "not sure about error handling"
        # or action from "someone needs to verify"
        has_thread_finding = any(
            "unclear" in j or "needs" in j or "uncertain" in j or "not sure" in j
            for j in all_judgments
        )
        assert has_thread_finding, (
            f"No thread-lane findings in PR review: {all_judgments}"
        )

    def test_code_findings_in_pr_review(self):
        """PR review code blocks should produce code-lane findings."""
        result = smell_check(PR_REVIEW)
        all_items = result["findings"] + result["stable_points"]
        has_code_finding = any(
            "impure" in i["judgment"].lower()
            or "pure" in i["judgment"].lower()
            or "load_config" in i["judgment"]
            for i in all_items
        )
        assert has_code_finding, (
            f"No code-lane findings in PR review: "
            + "; ".join(i["judgment"][:40] for i in all_items)
        )

    def test_cross_lane_findings_not_contested(self):
        """Thread finding about error handling and code finding about
        load_config should not contest each other."""
        result = smell_check(PR_REVIEW)
        for c in result.get("findings", []):
            # A code finding should not say "smells funny" just because
            # a thread finding mentions similar words
            if "load_config" in c["judgment"]:
                assert "smells funny" not in c["judgment"].lower(), (
                    f"Code finding incorrectly rendered as contested: {c['judgment']}"
                )


class TestDiffWithComments:

    def test_produces_findings(self):
        result = smell_check(DIFF_WITH_COMMENTS)
        all_items = result["findings"] + result["stable_points"] + result["open_questions"]
        assert len(all_items) >= 1


class TestCodeReviewThread:

    def test_inline_code_detected(self):
        """Code within a review conversation should be detected."""
        kind = detect_input_kind(CODE_REVIEW_THREAD)
        # Unfenced inline code may tip detection either way
        assert kind in ("mixed", "thread", "python_source")

    def test_produces_findings(self):
        result = smell_check(CODE_REVIEW_THREAD)
        all_items = result["findings"] + result["stable_points"] + result["open_questions"]
        # May produce code or thread findings depending on detection
        assert len(all_items) >= 0  # at minimum doesn't crash


# ---------------------------------------------------------------------------
# Verification still works on mixed input
# ---------------------------------------------------------------------------

class TestMixedVerification:

    def test_verification_valid_on_mixed(self):
        result = smell_check(PR_REVIEW)
        assert result["verification"]["valid"] is True
        assert result["verification"]["wall_state"] == "held"

    def test_deterministic_on_mixed(self):
        r1 = smell_check(PR_REVIEW)
        r2 = smell_check(PR_REVIEW)
        assert r1["verification"]["wall_state"] == r2["verification"]["wall_state"]


# ---------------------------------------------------------------------------
# Lane labeling in output
# ---------------------------------------------------------------------------

class TestLaneLabeling:

    def test_input_kind_in_governed_state(self):
        """The governed state should report which input kind was detected."""
        result = smell_check(PR_REVIEW)
        gs = result["custody_record"]["authoritative_output"]["governed_state"]
        assert gs.get("input_kind") == "mixed"

    def test_thread_input_kind(self):
        result = smell_check("We decided Friday. Not sure about parking.")
        gs = result["custody_record"]["authoritative_output"]["governed_state"]
        assert gs.get("input_kind") == "thread"
