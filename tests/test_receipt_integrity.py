"""Receipt integrity tests — the P0 fix.

The receipt must bind the FULL analysis payload, not just the summary.
verify must catch tampered payloads (e.g. violations zeroed out).

This is the operational test for the product promise:
"We detect unwitnessed operational claims."

If someone tampers with a receipt to hide violations, the receipt
is making an unwitnessed claim about the analysis result. The stamp
must expose that.
"""

import json
import sys
import copy
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.surface.stamp import (
    stamp, verify_stamp, h, GENESIS,
    hash_analysis_payload, verify_receipt_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analysis_result() -> dict:
    """A minimal but realistic analysis result."""
    return {
        "summary": {
            "total_files": 3,
            "total_functions": 12,
            "pure_count": 8,
            "impure_count": 4,
            "violation_count": 2,
            "type_counts": {"CONTRACT": 5, "CONSTRAINT": 3},
            "external_deps": ["requests"],
        },
        "violations": [
            {
                "type": "dangerous_deserialization",
                "function": "load_config",
                "file": "app.py",
                "line": 42,
                "message": "pickle.loads on untrusted input",
                "mother_type": "CONSTRAINT",
            },
            {
                "type": "code_execution",
                "function": "run_plugin",
                "file": "plugins.py",
                "line": 88,
                "message": "exec() with user-supplied code",
                "mother_type": "CONSTRAINT",
            },
        ],
        "files": {
            "app.py": {"functions": [{"name": "load_config", "pure": False}]},
        },
    }


def _build_receipt(result: dict) -> dict:
    """Build a receipt the same way substrate_cli.py does (post-fix)."""
    input_hash = h(b"test-source-files")
    fn_hash = h(b"test-analyzer")
    output_hash = hash_analysis_payload(result)

    s = stamp("analyze", input_hash, fn_hash, output_hash, GENESIS)

    return {
        "schema": "substrate.receipt.v1",
        "analysis": result,
        "stamp": {
            "schema": s.schema,
            "domain": s.domain,
            "input_hash": s.input_hash,
            "fn_hash": s.fn_hash,
            "output_hash": s.output_hash,
            "prev_stamp_hash": s.prev_stamp_hash,
            "stamp_hash": s.stamp_hash,
        },
        "metadata": {"tool": "substrate", "version": "0.1.0"},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReceiptPayloadBinding:
    """The receipt's output_hash must cover the full analysis."""

    def test_clean_receipt_verifies(self):
        """An untampered receipt passes both stamp and payload checks."""
        result = _make_analysis_result()
        receipt = _build_receipt(result)

        # Stamp self-hash
        from src.surface.stamp import Stamp
        stmp = Stamp(**receipt["stamp"])
        assert verify_stamp(stmp)

        # Payload binding
        ok, msg = verify_receipt_payload(receipt)
        assert ok, msg

    def test_tampered_violations_fails(self):
        """Zeroing out violations must fail payload verification.

        This is THE test: the code review showed that before the fix,
        you could set violations=[] and verify still passed.
        """
        result = _make_analysis_result()
        receipt = _build_receipt(result)

        # Tamper: hide all violations
        receipt["analysis"]["violations"] = []
        receipt["analysis"]["summary"]["violation_count"] = 0

        # Stamp self-hash still passes (we didn't touch the stamp)
        from src.surface.stamp import Stamp
        stmp = Stamp(**receipt["stamp"])
        assert verify_stamp(stmp), "stamp self-hash should still pass"

        # But payload binding MUST fail
        ok, msg = verify_receipt_payload(receipt)
        assert not ok, "tampered violations should fail payload check"
        assert "tampered" in msg.lower()

    def test_tampered_summary_fails(self):
        """Modifying the summary must also fail."""
        result = _make_analysis_result()
        receipt = _build_receipt(result)

        receipt["analysis"]["summary"]["pure_count"] = 999

        ok, _ = verify_receipt_payload(receipt)
        assert not ok

    def test_tampered_file_data_fails(self):
        """Modifying per-file analysis must fail."""
        result = _make_analysis_result()
        receipt = _build_receipt(result)

        receipt["analysis"]["files"]["app.py"]["functions"][0]["pure"] = True

        ok, _ = verify_receipt_payload(receipt)
        assert not ok

    def test_added_field_fails(self):
        """Adding a field to analysis must fail (payload changed)."""
        result = _make_analysis_result()
        receipt = _build_receipt(result)

        receipt["analysis"]["injected"] = "this was not witnessed"

        ok, _ = verify_receipt_payload(receipt)
        assert not ok

    def test_missing_analysis_fails(self):
        """Receipt with no analysis payload fails."""
        result = _make_analysis_result()
        receipt = _build_receipt(result)
        del receipt["analysis"]

        ok, msg = verify_receipt_payload(receipt)
        assert not ok
        assert "no analysis" in msg.lower()


class TestHashAnalysisPayloadDeterminism:
    """hash_analysis_payload must be deterministic and canonical."""

    def test_same_result_same_hash(self):
        r1 = _make_analysis_result()
        r2 = _make_analysis_result()
        assert hash_analysis_payload(r1) == hash_analysis_payload(r2)

    def test_key_order_irrelevant(self):
        """Dict key insertion order must not affect the hash."""
        r1 = {"b": 2, "a": 1, "c": {"z": 3, "y": 4}}
        r2 = {"a": 1, "c": {"y": 4, "z": 3}, "b": 2}
        assert hash_analysis_payload(r1) == hash_analysis_payload(r2)

    def test_different_content_different_hash(self):
        r1 = _make_analysis_result()
        r2 = _make_analysis_result()
        r2["violations"] = []
        assert hash_analysis_payload(r1) != hash_analysis_payload(r2)
