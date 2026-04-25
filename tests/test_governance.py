"""Growth governance tests.

These enforce the rules that prevent the atlas from collapsing
back into a heuristic pile:
- motif admission bar
- coverage completeness
- no orphan laws
- contrast pair coverage
"""

import sys
import os

sys.path.insert(0, os.path.expanduser("~/smell-check"))

from smell_check.atlas import (
    verify_all_motif_admissions,
    verify_pipeline_shape,
    coverage_report,
    ALL_MOTIFS,
    ALL_LAWS,
    CONTRAST_PAIRS,
    CORPUS_TIERS,
)


def test_all_motifs_pass_admission():
    """Every motif must meet the full admission bar."""
    ok, errors = verify_all_motif_admissions()
    if not ok:
        print(f"  Admission failures:")
        for e in errors:
            print(f"    {e}")
    assert ok, f"Motif admission failures: {errors}"


def test_pipeline_shape():
    """Pipeline infrastructure must be intact."""
    ok, errors = verify_pipeline_shape()
    assert ok, f"Pipeline shape errors: {errors}"


def test_no_orphan_laws():
    """Every defined law must be referenced by at least one motif."""
    report = coverage_report()
    orphans = report["orphan_laws"]
    assert not orphans, f"Orphan laws (defined but unused): {orphans}"


def test_coverage_dashboard():
    """Coverage dashboard must report without errors."""
    report = coverage_report()

    print(f"\n  Coverage Dashboard:")
    print(f"    Motifs: {report['total_motifs']}")
    print(f"    Laws: {report['total_laws']}")
    print(f"    Contrast pairs: {report['total_contrast_pairs']}")

    missing = report["motifs_missing_anti_examples"]
    if missing:
        print(f"    WARNING: motifs missing anti-examples: {missing}")

    single = report["single_user_laws"]
    if single:
        print(f"    INFO: single-user laws: {single}")

    no_contrast = report["motifs_without_contrast_pairs"]
    if no_contrast:
        print(f"    INFO: motifs without contrast pairs: {no_contrast}")

    # Dashboard must produce valid output
    assert report["total_motifs"] >= 8, "Expected at least 8 motifs"
    assert report["total_laws"] >= 10, "Expected at least 10 laws"
    assert report["total_contrast_pairs"] >= 3, "Expected at least 3 contrast pairs"


def test_corpus_tiers_defined():
    """All four corpus tiers must be defined."""
    required = {"experimental", "sentinel", "hard_gate", "atlas_seed"}
    assert required <= set(CORPUS_TIERS.keys()), \
        f"Missing corpus tiers: {required - set(CORPUS_TIERS.keys())}"


if __name__ == "__main__":
    tests = [
        test_all_motifs_pass_admission,
        test_pipeline_shape,
        test_no_orphan_laws,
        test_coverage_dashboard,
        test_corpus_tiers_defined,
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
