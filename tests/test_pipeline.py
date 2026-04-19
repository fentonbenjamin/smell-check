"""Pipeline and projection tests — the MVP vertical slice.

Tests the full trunk: text → perceive → type → judge → receipt → governed state.
Tests both projection lenses: consumer and pro.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.surface.pipeline import analyze_thread, analyze_thread_multi
from src.surface.projections import project_consumer, project_pro


# ---------------------------------------------------------------------------
# Pipeline: single thread analysis
# ---------------------------------------------------------------------------

class TestAnalyzeThread:

    def test_returns_governed_state(self):
        result = analyze_thread("The server guarantees sub-10ms latency")
        assert "governed_state" in result
        assert "receipt_chain" in result
        assert "topic_context" in result

    def test_governed_state_has_correct_shape(self):
        result = analyze_thread("We decided to use PostgreSQL for the main database")
        gs = result["governed_state"]
        assert "promoted" in gs
        assert "contested" in gs
        assert "deferred" in gs
        assert "loss" in gs
        assert "typed_units" in gs
        assert "classification" in gs

    def test_receipt_chain_exists(self):
        result = analyze_thread("The API returns JSON responses")
        chain = result["receipt_chain"]
        assert "stamps" in chain
        assert "chain_length" in chain
        assert chain["chain_length"] >= 1

    def test_stamps_are_valid_dicts(self):
        result = analyze_thread("Tests must pass before deploy")
        for stamp in result["receipt_chain"]["stamps"]:
            assert "stamp_hash" in stamp
            assert "input_hash" in stamp
            assert "fn_hash" in stamp
            assert "output_hash" in stamp
            assert "domain" in stamp

    def test_deterministic_same_input_same_stamps(self):
        text = "The cache invalidation strategy uses TTL-based expiry"
        r1 = analyze_thread(text, topic_handle="cache")
        r2 = analyze_thread(text, topic_handle="cache")
        stamps1 = r1["receipt_chain"]["stamps"]
        stamps2 = r2["receipt_chain"]["stamps"]
        for s1, s2 in zip(stamps1, stamps2):
            assert s1["stamp_hash"] == s2["stamp_hash"]

    def test_topic_context_preserved(self):
        result = analyze_thread(
            "Deploy to staging first",
            topic_handle="deploy-plan",
            topic_keywords={"deploy", "staging"},
        )
        ctx = result["topic_context"]
        assert ctx["handle"] == "deploy-plan"
        assert "deploy" in ctx["keywords"]

    def test_keywords_inferred_when_not_provided(self):
        result = analyze_thread("The database migration requires downtime")
        ctx = result["topic_context"]
        assert len(ctx["keywords"]) > 0

    def test_empty_text_produces_minimal_state(self):
        result = analyze_thread("")
        gs = result["governed_state"]
        # Empty text should not crash, may produce empty or minimal state
        assert isinstance(gs["promoted"], list)


# ---------------------------------------------------------------------------
# Pipeline: multi-turn thread
# ---------------------------------------------------------------------------

class TestAnalyzeThreadMulti:

    def test_multi_turn_aggregates(self):
        turns = [
            {"text": "Let's deploy on Friday", "actor": "alice"},
            {"text": "Sounds good, I'll prepare the migration", "actor": "bob"},
            {"text": "Wait, we haven't tested the rollback path yet", "actor": "alice"},
        ]
        result = analyze_thread_multi(turns, topic_handle="deploy")
        gs = result["governed_state"]
        assert isinstance(gs["promoted"], list)
        assert result["receipt_chain"]["chain_length"] >= 3

    def test_keywords_accumulate_across_turns(self):
        turns = [
            {"text": "The server uses Redis for caching and guarantees sub-millisecond reads for all hot keys in the infra layer"},
            {"text": "We should also add PostgreSQL for persistence in the infra database tier to handle cold storage"},
        ]
        result = analyze_thread_multi(turns, topic_handle="infra", topic_keywords={"infra"})
        ctx = result["topic_context"]
        # Keywords should have grown beyond the seed
        assert len(ctx["keywords"]) > 1

    def test_empty_turns_skipped(self):
        turns = [
            {"text": "Real content here"},
            {"text": ""},
            {"text": "   "},
            {"text": "More real content"},
        ]
        result = analyze_thread_multi(turns)
        # Should not crash on empty turns
        assert result["receipt_chain"]["chain_length"] >= 2


# ---------------------------------------------------------------------------
# Consumer projection
# ---------------------------------------------------------------------------

class TestConsumerProjection:

    def _make_state(self, promoted=None, contested=None, deferred=None):
        return {
            "promoted": promoted or [],
            "contested": contested or [],
            "deferred": deferred or [],
            "loss": [],
        }

    def test_contract_becomes_decided(self):
        state = self._make_state(promoted=[
            {"text": "We will use PostgreSQL", "mother_type": "CONTRACT"},
        ])
        cards = project_consumer(state)
        assert len(cards["decided"]) == 1
        assert cards["decided"][0]["text"] == "We will use PostgreSQL"

    def test_uncertainty_becomes_unclear(self):
        state = self._make_state(promoted=[
            {"text": "We haven't tested under load", "mother_type": "UNCERTAINTY"},
        ])
        cards = project_consumer(state)
        assert len(cards["unclear"]) == 1

    def test_constraint_becomes_needs_confirmation(self):
        state = self._make_state(promoted=[
            {"text": "Port 8080 must be available", "mother_type": "CONSTRAINT"},
        ])
        cards = project_consumer(state)
        assert len(cards["needs_confirmation"]) == 1

    def test_contested_becomes_needs_confirmation(self):
        state = self._make_state(contested=[
            {"text": "The deadline is Friday"},
        ])
        cards = project_consumer(state)
        assert len(cards["needs_confirmation"]) == 1

    def test_deferred_becomes_waiting_on(self):
        state = self._make_state(deferred=[
            {"text": "Check with legal", "_defer_reason": "no evidence"},
        ])
        cards = project_consumer(state)
        assert len(cards["waiting_on"]) == 1

    def test_actionable_text_becomes_to_do(self):
        state = self._make_state(promoted=[
            {"text": "Need to send the contract by Thursday", "mother_type": "CONTRACT"},
        ])
        cards = project_consumer(state)
        assert len(cards["to_do"]) == 1

    def test_summary_counts(self):
        state = self._make_state(
            promoted=[
                {"text": "Decision A", "mother_type": "CONTRACT"},
                {"text": "Not sure about B", "mother_type": "UNCERTAINTY"},
            ],
            deferred=[{"text": "Maybe C"}],
        )
        cards = project_consumer(state)
        s = cards["summary"]
        assert s["decided_count"] == 1
        assert s["unclear_count"] == 1
        assert s["waiting_on_count"] == 1


# ---------------------------------------------------------------------------
# Pro projection
# ---------------------------------------------------------------------------

class TestProProjection:

    def _make_state(self, promoted=None, contested=None, deferred=None):
        return {
            "promoted": promoted or [],
            "contested": contested or [],
            "deferred": deferred or [],
            "loss": [],
        }

    def test_contract_becomes_commitment_and_safe(self):
        state = self._make_state(promoted=[
            {"text": "API returns JSON", "mother_type": "CONTRACT"},
        ])
        cards = project_pro(state)
        assert len(cards["commitments"]) == 1
        assert len(cards["safe_to_rely_on"]) == 1

    def test_constraint_becomes_constraint_and_safe(self):
        state = self._make_state(promoted=[
            {"text": "Must run on port 443", "mother_type": "CONSTRAINT"},
        ])
        cards = project_pro(state)
        assert len(cards["constraints"]) == 1
        assert len(cards["safe_to_rely_on"]) == 1

    def test_witness_becomes_evidence_and_safe(self):
        state = self._make_state(promoted=[
            {"text": "Observed in production logs", "mother_type": "WITNESS"},
        ])
        cards = project_pro(state)
        assert len(cards["evidence"]) == 1
        assert len(cards["safe_to_rely_on"]) == 1

    def test_uncertainty_becomes_still_uncertain(self):
        state = self._make_state(promoted=[
            {"text": "Unclear if this scales", "mother_type": "UNCERTAINTY"},
        ])
        cards = project_pro(state)
        assert len(cards["still_uncertain"]) == 1

    def test_contested_becomes_needs_judgment(self):
        state = self._make_state(contested=[
            {"text": "Conflicting requirements"},
        ])
        cards = project_pro(state)
        assert len(cards["needs_human_judgment"]) == 1

    def test_deferred_becomes_needs_judgment(self):
        state = self._make_state(deferred=[
            {"text": "No evidence yet", "_defer_reason": "insufficient"},
        ])
        cards = project_pro(state)
        assert len(cards["needs_human_judgment"]) == 1

    def test_summary_counts(self):
        state = self._make_state(
            promoted=[
                {"text": "Fact A", "mother_type": "CONTRACT"},
                {"text": "Unsure B", "mother_type": "UNCERTAINTY"},
            ],
            contested=[{"text": "Contested C"}],
        )
        cards = project_pro(state)
        s = cards["summary"]
        assert s["safe_count"] == 1
        assert s["uncertain_count"] == 1
        assert s["judgment_count"] == 1


# ---------------------------------------------------------------------------
# End-to-end: pipeline → projection
# ---------------------------------------------------------------------------

class TestEndToEnd:

    def test_pipeline_to_consumer_projection(self):
        result = analyze_thread(
            "We decided to ship on Friday, but we haven't tested the rollback path yet",
            topic_handle="release",
        )
        cards = project_consumer(result["governed_state"])
        # Should have at least one card across all categories
        total = sum(cards["summary"].values())
        assert total > 0

    def test_pipeline_to_pro_projection(self):
        result = analyze_thread(
            "The API guarantees backwards compatibility but the migration is untested",
            topic_handle="api-review",
        )
        cards = project_pro(result["governed_state"])
        total = sum(cards["summary"].values())
        assert total > 0

    def test_same_input_different_projections(self):
        """Same governed state, two different lenses."""
        result = analyze_thread(
            "Port 8080 must be available and we need to confirm the DNS settings",
            topic_handle="infra",
        )
        gs = result["governed_state"]
        consumer = project_consumer(gs)
        pro = project_pro(gs)

        # Both should have content
        assert sum(consumer["summary"].values()) > 0
        assert sum(pro["summary"].values()) > 0

        # Consumer uses plain language keys
        assert "decided" in consumer
        assert "to_do" in consumer

        # Pro uses review language keys
        assert "commitments" in pro
        assert "constraints" in pro
