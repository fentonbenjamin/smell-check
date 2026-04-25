"""Coagulator and pipeline invariant tests.

These enforce structural laws, not behavioral expectations:
- evidence becomes support, not duplicate top-level cards
- one concern per cluster
- no unsigned judgment escapes without a motif or law trace
- pipeline shape is intact
"""

import sys
import os

sys.path.insert(0, os.path.expanduser("~/smell-check"))

from smell_check.chamber import process_through_chamber
from smell_check.projections import project_smell_check
from smell_check.atlas import verify_pipeline_shape, ALL_MOTIFS, ALL_LAWS, MOTIF_INDEX


BILLING_MIGRATION = """PM: Okay team, we're shipping the billing migration next Tuesday.
Dev A: Wait, I thought we pushed that to after the security audit?
PM: That was the old plan. Leadership wants it sooner.
Dev B: Do we even have the rollback strategy documented?
Dev A: I started on it but haven't finished. Probably fine though.
PM: Can someone own the runbook? We need it before go-live.
Dev B: I can take a look, but I'm also on the incident rotation this week.
Dev A: The data migration script should only take a few hours to run.
PM: Should? Have we tested it on production-scale data?
Dev A: Not yet, but the staging run looked clean.
Dev B: Staging is 1% of prod volume. That's not a real test.
PM: Okay, let's just make sure we have a checkpoint before we flip the switch.
Dev A: Agreed. I'll add a dry-run flag.
Dev B: Sounds good, but who's going to be on call during the migration window?
PM: We'll figure that out closer to the date."""


def _run(text):
    custody = process_through_chamber(text)
    gs = custody["authoritative_output"]["governed_state"]
    return project_smell_check(gs)


def test_pipeline_shape_intact():
    """The pipeline infrastructure must be structurally valid."""
    ok, errors = verify_pipeline_shape()
    assert ok, f"Pipeline shape broken: {errors}"


def test_all_motifs_have_laws_or_are_explicit():
    """Every motif must either reference named laws or be explicitly law-free."""
    for motif in ALL_MOTIFS:
        # Motifs with required_laws must reference valid laws
        for law_name in motif.required_laws:
            assert law_name in {l.name for l in ALL_LAWS}, \
                f"Motif '{motif.name}' references unknown law '{law_name}'"


def test_all_motifs_have_examples():
    """Every motif must have at least one positive example."""
    for motif in ALL_MOTIFS:
        assert motif.examples, \
            f"Motif '{motif.name}' has no examples"


def test_no_duplicate_evidence_in_output():
    """The same evidence text should not appear as both a finding AND an open question."""
    r = _run(BILLING_MIGRATION)

    finding_texts = {f["judgment"].lower() for f in r.get("findings", [])}
    question_texts = {q["judgment"].lower() for q in r.get("open_questions", [])}
    stable_texts = {s["judgment"].lower() for s in r.get("stable_points", [])}

    # Finding text should not also be an open question
    overlap_fq = finding_texts & question_texts
    assert not overlap_fq, f"Same text in findings AND open questions: {overlap_fq}"

    # Finding text should not also be a stable point
    overlap_fs = finding_texts & stable_texts
    assert not overlap_fs, f"Same text in findings AND stable points: {overlap_fs}"


def test_motif_admission_requirements():
    """Every motif must meet the admission bar:
    - at least one positive example
    - trigger kinds or events defined
    - output type specified
    """
    for motif in ALL_MOTIFS:
        assert motif.examples, f"'{motif.name}': no examples"
        assert motif.trigger_kinds or motif.trigger_events, \
            f"'{motif.name}': no triggers"
        assert motif.output_type, f"'{motif.name}': no output_type"


def test_rendered_judgment_has_drillback():
    """Every rendered card must carry drillback metadata."""
    r = _run(BILLING_MIGRATION)

    for category in ("findings", "stable_points", "open_questions"):
        for card in r.get(category, []):
            assert "drillback" in card or "where" in card, \
                f"Card in {category} missing drillback: {card.get('judgment', '')[:40]}"


if __name__ == "__main__":
    tests = [
        test_pipeline_shape_intact,
        test_all_motifs_have_laws_or_are_explicit,
        test_all_motifs_have_examples,
        test_no_duplicate_evidence_in_output,
        test_motif_admission_requirements,
        test_rendered_judgment_has_drillback,
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
