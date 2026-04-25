"""Emit a canonical packet from a smell check run.

Usage:
    echo "your text here" | python -m smell_check.emit_packet
    python -m smell_check.emit_packet --file code.py

Produces a canonical packet JSON on stdout that the chamber kernel can consume.
"""

import json
import sys
import hashlib
import platform
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from smell_check.chamber import process_through_chamber, measure_chamber
from smell_check.stamp import h, _canonical_json


def emit_packet(text: str) -> str:
    """Run smell check and emit a canonical packet for the chamber kernel."""

    # Run through the chamber to get governed state + receipts
    custody = process_through_chamber(text)

    gs = custody["authoritative_output"]["governed_state"]
    chain = custody["authoritative_output"]["receipt_chain"]
    blob = custody["staged_blob"]
    chamber = custody["chamber_measurement"]

    # Build the canonical packet
    packet = {
        "input": {
            "blob_hash": blob["blob_hash"],
            "byte_length": blob["byte_length"],
            "content_type": blob.get("content_type", "text/plain"),
            "capture_source": blob.get("capture_source", "paste"),
        },
        "perception": {
            "perception_mode": "heuristic",
            "input_kind": gs.get("input_kind", "thread"),
            "tagger_fn_hash": chamber["perceiver_hash"],
            "sieve_fn_hash": chamber["sieve_hash"],
            "analyzer_fn_hash": chamber.get("analyzer_hash"),
            "pipeline_fn_hash": h(Path(__file__).parent.joinpath("pipeline.py").read_bytes()),
        },
        "result": {
            "governed_state_hash": h(_canonical_json(gs).encode()),
            "promoted_count": len(gs.get("promoted", [])),
            "contested_count": len(gs.get("contested", [])),
            "deferred_count": len(gs.get("deferred", [])),
            "loss_count": len(gs.get("loss", [])),
        },
        "chain": {
            "stamp_chain_tip": chain["tip_hash"] or h(b"genesis"),
            "stamp_chain_length": chain["chain_length"],
            "prev_chamber_receipt": None,
        },
        "metadata": {
            "timestamp": custody["staged_blob"]["capture_ts"],
            "smell_check_version": "0.1.0",
            "platform": f"{sys.platform}-{platform.machine()}",
        },
    }

    return json.dumps(packet, indent=2, default=str)


def main():
    # Read from file or stdin
    if "--file" in sys.argv:
        idx = sys.argv.index("--file")
        if idx + 1 < len(sys.argv):
            text = Path(sys.argv[idx + 1]).read_text()
        else:
            print("error: --file requires a path", file=sys.stderr)
            sys.exit(1)
    else:
        text = sys.stdin.read()

    if not text.strip():
        print("error: empty input", file=sys.stderr)
        sys.exit(1)

    packet_json = emit_packet(text)
    print(packet_json)


if __name__ == "__main__":
    main()
