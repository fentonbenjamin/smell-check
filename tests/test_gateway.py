"""Gateway tests — one tool, one name: smell_check."""

import sys
from pathlib import Path

import pytest

# sys.path handled by package layout

from smell_check.gateway import smell_check


class TestSmellCheck:

    def test_returns_findings_shape(self):
        result = smell_check("We decided on Friday pickup at 3pm")
        assert "summary" in result
        assert "findings" in result
        assert "stable_points" in result
        assert "open_questions" in result
        assert "verification" in result
        assert "custody_record" in result

    def test_verification_is_valid(self):
        result = smell_check("The server guarantees backwards compatibility")
        v = result["verification"]
        assert v["valid"] is True
        assert v["wall_state"] == "held"

    def test_findings_are_human_readable(self):
        result = smell_check(
            "Someone needs to confirm the restaurant reservation."
        )
        # Should have at least one finding with judgment/because/what_to_do
        all_items = result["findings"] + result["stable_points"] + result["open_questions"]
        assert len(all_items) > 0
        for item in all_items:
            assert "judgment" in item
            assert "because" in item

    def test_findings_have_drillback(self):
        result = smell_check("We decided on Friday. Not sure about parking.")
        all_items = result["findings"] + result["stable_points"] + result["open_questions"]
        for item in all_items:
            assert "drillback" in item

    def test_human_coordination_thread(self):
        result = smell_check(
            "We decided on 3pm pickup Friday. Not sure if Sarah can make it. "
            "Someone needs to confirm the restaurant. John said he would "
            "handle the cake. I have not heard back from him yet.",
        )
        # Should produce findings + stable + open questions
        total = len(result["findings"]) + len(result["stable_points"]) + len(result["open_questions"])
        assert total >= 3
        assert result["verification"]["valid"] is True
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0

    def test_stable_thread_produces_stable_summary(self):
        result = smell_check("We decided on Friday. The plan is confirmed.")
        # Should be mostly stable
        assert len(result["stable_points"]) >= 1

    def test_uncertain_thread_produces_open_questions(self):
        result = smell_check("Not sure if this works. Might need to revisit.")
        assert len(result["open_questions"]) >= 1

    def test_raw_code_does_not_produce_false_decided(self):
        """Raw code should abstain, not claim it's decided."""
        result = smell_check(
            "def load_config(path):\n    return pickle.loads(open(path, 'rb').read())"
        )
        for sp in result.get("stable_points", []):
            assert "decided" not in sp["judgment"].lower(), (
                f"Raw code falsely marked as decided: {sp['judgment']}"
            )

    def test_diff_does_not_produce_false_decided(self):
        """A diff snippet should not be treated as a decision."""
        result = smell_check(
            "diff --git a/app.py b/app.py\n- old_line\n+ new_line"
        )
        for sp in result.get("stable_points", []):
            assert "decided" not in sp["judgment"].lower(), (
                f"Diff falsely marked as decided: {sp['judgment']}"
            )


class TestVerifyLoop:

    def test_smell_check_then_verify(self):
        from smell_check.chamber import verify_custody
        result = smell_check("Test the full loop")
        custody = result["custody_record"]
        verification = verify_custody(custody)
        assert verification["valid"] is True
        assert verification["wall_state"] == "held"

    def test_tampered_record_fails(self):
        from smell_check.chamber import verify_custody
        result = smell_check(
            "The server guarantees sub-10ms latency for all endpoints"
        )
        custody = result["custody_record"]
        custody["authoritative_output"]["governed_state"]["promoted"].append(
            {"text": "INJECTED", "mother_type": "CONTRACT"}
        )
        verification = verify_custody(custody)
        assert not verification["valid"]
        assert verification["wall_state"] == "broken"


class TestMCPRegistration:

    def test_one_tool_registered(self):
        from smell_check.gateway import mcp
        tools = mcp._tool_manager._tools
        assert "smell_check" in tools
        assert len(tools) == 1

    def test_server_name(self):
        from smell_check.gateway import mcp
        assert mcp.name == "Smell Check"
