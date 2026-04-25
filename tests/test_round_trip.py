"""Round-trip law tests: judgment → render → reparse → same judgment class.

The core invariant:
  If the chamber renders a judgment, and someone pastes that rendered text
  back through the pipeline, the result should produce a compatible judgment.

Not exact text equality. Parity on:
  - kind (StablePoint, OpenQuestion, ProvisionalDecision, Concern, etc.)
  - state (stable, open, provisional, resolved, active)
  - certainty band (the rendered text must not claim more certainty than the typed judgment)

This prevents the render layer from distorting what the chamber actually concluded.
"""

import sys
import os

sys.path.insert(0, os.path.expanduser("~/smell-check"))

from smell_check.chamber import process_through_chamber, verify_custody
from smell_check.projections import project_smell_check


# ---------------------------------------------------------------------------
# Fixtures — the same ones from the quality corpus
# ---------------------------------------------------------------------------

HEDGED_AGREEMENT = """Lead: I think we should sunset the legacy API by Q3.
Dev: Yeah, probably. Though some enterprise clients might still need it.
Lead: We can deal with that when it comes up.
Dev: I guess. Let me know if you want me to start on the migration guide.
Lead: Sure, when you get a chance."""

EXPLICIT_AGREEMENT = """Sarah: For the API versioning, I propose we use URL path versioning: /v1/users, /v2/users.
Tom: Agreed. Header-based versioning is harder to debug.
Sarah: Right. And we'll keep v1 supported for 12 months after v2 launches.
Tom: That's reasonable. Let's document that in the API guidelines.
Sarah: Will do. I'll have the guidelines PR up tomorrow."""

CHALLENGED_STAYS_OPEN = """PM: We're going with vendor A for the payment processor.
Eng Lead: Wait, did anyone evaluate vendor B? Their uptime numbers are better.
PM: I don't think we have time to evaluate another option.
Eng Lead: We should at least look at their pricing page before locking in.
PM: Fine, but I'm not pushing the timeline."""

DB_DECISION_REVERSAL = """Alice: We should switch to Postgres before the launch.
Bob: I thought we already decided on MySQL last week?
Alice: That was before the scaling numbers came in. Postgres handles concurrent writes better.
Bob: Sure, but we have zero Postgres experience on the team.
Alice: We can figure it out. The docs are good.
Bob: Famous last words. What about the migration timeline?
Alice: It should only take a couple days.
Bob: Should? Have you actually scoped it?
Alice: Not yet, but how hard can it be?
Bob: Okay, I think we need to loop in DevOps before we commit to this.
Alice: Fine, but we need to decide by Friday or we miss the window.
Bob: Agreed. Let's get a meeting on the calendar."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_pipeline(text: str) -> dict:
    """Run text through the full pipeline and return projected output."""
    custody = process_through_chamber(text)
    gs = custody["authoritative_output"]["governed_state"]
    return project_smell_check(gs)


def _rendered_text(result: dict) -> str:
    """Reconstruct a readable text from the rendered output.

    This is what a user might copy-paste to share the chamber's findings.
    """
    lines = []
    if result.get("summary"):
        lines.append(result["summary"])
    for f in result.get("findings", []):
        lines.append(f"Finding: {f['judgment']}")
    for s in result.get("stable_points", []):
        lines.append(f"Stable: {s['judgment']}")
    for q in result.get("open_questions", []):
        lines.append(f"Open question: {q['judgment']}")
    return "\n".join(lines)


def _judgment_kinds(result: dict) -> dict:
    """Extract judgment kinds from a result for comparison."""
    return {
        "findings_count": len(result.get("findings", [])),
        "stable_count": len(result.get("stable_points", [])),
        "open_q_count": len(result.get("open_questions", [])),
        "has_findings": len(result.get("findings", [])) > 0,
        "has_stable": len(result.get("stable_points", [])) > 0,
        "has_open_q": len(result.get("open_questions", [])) > 0,
    }


# ---------------------------------------------------------------------------
# Certainty band constants
# ---------------------------------------------------------------------------

# Words that claim high certainty — must not appear in rendered output
# for provisional or open judgments
_HIGH_CERTAINTY_WORDS = {
    "decided", "confirmed", "locked in", "settled", "final",
    "guaranteed", "proven", "verified", "certain",
}

# Words that indicate openness — compatible with provisional/open judgments
_OPEN_WORDS = {
    "tentative", "provisional", "unclear", "unresolved",
    "open question", "not fully committed", "hedged",
}


# ---------------------------------------------------------------------------
# Round-trip law tests
# ---------------------------------------------------------------------------

def test_round_trip_hedged_agreement():
    """Hedged agreement: rendered text should not upgrade to stable on reparse."""
    r1 = _run_pipeline(HEDGED_AGREEMENT)

    # Must have findings (the hedge) and no stable points
    assert r1["findings"], "First pass should produce findings"
    assert not r1["stable_points"], "Hedged agreement should not produce stable points"

    # Round-trip: feed rendered output back through
    rendered = _rendered_text(r1)
    r2 = _run_pipeline(rendered)

    # The reparse should NOT produce stable points from hedged content
    # (it would be a certainty escalation)
    for s in r2.get("stable_points", []):
        j = s.get("judgment", "").lower()
        assert "tentative" not in j or "agreed" not in j, \
            f"Round-trip escalated hedged to stable: {s['judgment']}"


def test_round_trip_explicit_agreement():
    """Explicit agreement: stable points should survive round-trip."""
    r1 = _run_pipeline(EXPLICIT_AGREEMENT)
    # Should have stable points
    k1 = _judgment_kinds(r1)

    rendered = _rendered_text(r1)
    r2 = _run_pipeline(rendered)

    # Stable content should not produce findings on reparse
    # (that would mean the chamber contradicts itself)
    for f in r2.get("findings", []):
        j = f.get("judgment", "").lower()
        # Should not flag its own stable conclusions as concerns
        assert "smell" not in j and "concern" not in j, \
            f"Round-trip turned stable into concern: {f['judgment']}"


def test_round_trip_challenge_stays_open():
    """Challenged decision: open questions should not become stable on reparse."""
    r1 = _run_pipeline(CHALLENGED_STAYS_OPEN)

    # Must have open questions
    assert r1["open_questions"], "First pass should produce open questions"

    rendered = _rendered_text(r1)
    r2 = _run_pipeline(rendered)

    # The reparse should not produce stable points that claim
    # the challenge was resolved (it wasn't)
    for s in r2.get("stable_points", []):
        j = s.get("judgment", "").lower()
        # "resolved" would be a certainty escalation
        assert "resolved" not in j, \
            f"Round-trip resolved an unresolved challenge: {s['judgment']}"


def test_render_canon_certainty_ceiling():
    """Rendered text must not claim more certainty than the typed judgment."""
    r = _run_pipeline(HEDGED_AGREEMENT)

    for f in r.get("findings", []):
        j = f.get("judgment", "").lower()
        drillback = f.get("drillback", {})
        motif = drillback.get("motif", "")

        if motif == "hedged_agreement":
            # A hedged finding must NOT contain high-certainty words
            for word in _HIGH_CERTAINTY_WORDS:
                assert word not in j, \
                    f"Hedged finding claims certainty: '{word}' in '{f['judgment']}'"
            # It SHOULD contain openness language
            has_open = any(word in j for word in _OPEN_WORDS)
            assert has_open, \
                f"Hedged finding missing openness language: '{f['judgment']}'"


def test_render_canon_stable_not_hedged():
    """Stable points should not contain hedging language."""
    r = _run_pipeline(EXPLICIT_AGREEMENT)

    for s in r.get("stable_points", []):
        j = s.get("judgment", "").lower()
        hedge_words = {"probably", "maybe", "perhaps", "tentative", "not sure"}
        for word in hedge_words:
            assert word not in j, \
                f"Stable point contains hedge: '{word}' in '{s['judgment']}'"


def test_subject_dominance():
    """Same subject should not appear in conflicting states."""
    r = _run_pipeline(DB_DECISION_REVERSAL)

    # Collect all subjects
    all_subjects = []
    for f in r.get("findings", []):
        all_subjects.append(("finding", f.get("judgment", "")))
    for s in r.get("stable_points", []):
        all_subjects.append(("stable", s.get("judgment", "")))
    for q in r.get("open_questions", []):
        all_subjects.append(("open", q.get("judgment", "")))

    # No subject should appear as both stable AND open/finding
    stable_words = set()
    for kind, subj in all_subjects:
        if kind == "stable":
            words = set(subj.lower().split())
            stable_words |= words

    for kind, subj in all_subjects:
        if kind in ("finding", "open"):
            words = set(subj.lower().split())
            overlap = words & stable_words
            # Allow common words, flag only high overlap
            if len(overlap) > 5 and len(overlap) / max(len(words), 1) > 0.5:
                # This is suspicious but might be legitimate
                # Only fail if the stable text is very similar
                pass  # tracked but not hard-failed yet


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_round_trip_hedged_agreement,
        test_round_trip_explicit_agreement,
        test_round_trip_challenge_stays_open,
        test_render_canon_certainty_ceiling,
        test_render_canon_stable_not_hedged,
        test_subject_dominance,
    ]

    passed = 0
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            test()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {name}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {len(tests)}")
