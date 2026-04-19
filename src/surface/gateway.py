"""Gateway server — two doors, one chamber.

HTTP face: for iOS app, web clients, curl
MCP face: for Claude Code, Messages API, any MCP host

Both faces call the same chamber. The chamber decides.
The host presents. MCP is the channel, not the product.

Usage:
    # HTTP server (for iOS app, web clients, curl)
    .venv/bin/python -m surface.gateway

    # MCP over stdio (for Claude Code local config)
    .venv/bin/python -m surface.gateway --stdio
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .chamber import process_through_chamber, verify_custody, measure_chamber
from .projections import project_smell_check, project_consumer, project_pro

PORT = int(os.environ.get("GATEWAY_PORT", "8800"))

# ---------------------------------------------------------------------------
# MCP server — the tool face
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Smell Check",
    instructions=(
        "Smell Check analyzes threads, conversations, and code for what's actually being said. "
        "It classifies every claim into governance types, judges what's decided vs uncertain "
        "vs contested, and proves the analysis wasn't tampered with. "
        "Use smell_check whenever a user wants to understand what a thread means, "
        "what's safe to rely on, or whether something smells off."
    ),
)


@mcp.tool()
def smell_check(
    text: str,
    topic: str = "default",
) -> dict[str, Any]:
    """Smell check a thread, conversation, or code review.

    Give it any text — a group chat, email thread, PR discussion, agent
    transcript, contractor exchange — and it tells you what smells:

    - findings: what smells funny and why
    - stable_points: what looks decided or reported
    - open_questions: what's still unclear or unresolved

    Every judgment is receipted inside a measured chamber. The verification
    result tells you whether the analysis is intact.

    Args:
        text: The text to smell check.
        topic: A label for context (e.g., "deploy", "contractor-thread").
    """
    custody = process_through_chamber(text, topic_handle=topic)
    verification = verify_custody(custody)
    gs = custody["authoritative_output"]["governed_state"]

    # Primary output: findings-first smell check
    smell = project_smell_check(gs)

    return {
        # Layer 1: Human-readable interpretation
        "summary": smell["summary"],
        "findings": smell["findings"],
        "stable_points": smell["stable_points"],
        "open_questions": smell["open_questions"],
        # Layer 2: Verification
        "verification": {
            "valid": verification["valid"],
            "checks": verification["checks_performed"],
            "wall_state": verification["wall_state"],
            "perception_mode": verification["perception_mode"],
            "execution_class": verification["execution_class"],
            "security_mode": verification["security_mode"],
        },
        # Layer 3: Drillback
        "receipt_chain": {
            "length": custody["authoritative_output"]["receipt_chain"]["chain_length"],
            "tip": custody["authoritative_output"]["receipt_chain"]["tip_hash"],
        },
        "custody_record": custody,
    }


# ---------------------------------------------------------------------------
# HTTP face — for iOS app and web clients
# ---------------------------------------------------------------------------

def _build_http_app():
    """Build a simple ASGI app for the HTTP face.

    Routes:
        POST /threads/analyze — same as MCP analyze_thread
        POST /threads/verify  — same as MCP verify_custody_record
        GET  /health          — chamber status
        GET  /                — consumer web page
    """
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

    class HTTPHandler(BaseHTTPRequestHandler):

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/health":
                m = measure_chamber()
                self._json(200, {
                    "status": "ok",
                    "chamber_hash": m["chamber_hash"][:16] + "...",
                })
            elif path == "/":
                self._serve_page()
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            if path == "/threads/analyze":
                self._handle_analyze()
            elif path == "/threads/verify":
                self._handle_verify()
            else:
                self._json(404, {"error": "not found"})

        def do_OPTIONS(self):
            self.send_response(200)
            self._cors()
            self.end_headers()

        def _handle_analyze(self):
            try:
                body = self._read_body()
            except Exception as e:
                self._json(400, {"error": str(e)})
                return

            text = body.get("text", "")
            if not text:
                self._json(400, {"error": "provide 'text'"})
                return

            topic = body.get("topic", "default")

            try:
                result = smell_check(text, topic=topic)
                self._json(200, result)
            except Exception as e:
                self._json(500, {"error": f"analysis failed: {e}"})

        def _handle_verify(self):
            try:
                body = self._read_body()
            except Exception as e:
                self._json(400, {"error": str(e)})
                return

            try:
                # Unwrap if this is a smell_check result with nested custody_record
                if "custody_record" in body and "boundary_attestation" not in body:
                    body = body["custody_record"]
                result = verify_custody(body)
                self._json(200, result)
            except Exception as e:
                self._json(500, {"error": f"verification failed: {e}"})

        def _serve_page(self):
            from .thread_server import _CONSUMER_PAGE
            html = _CONSUMER_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self._cors()
            self.end_headers()
            self.wfile.write(html)

        def _json(self, status: int, data: dict):
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                raise ValueError("empty body")
            return json.loads(self.rfile.read(length))

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def log_message(self, fmt, *args):
            pass

    return ThreadingHTTPServer, HTTPHandler


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    mode = "http"
    port = PORT

    for i, arg in enumerate(sys.argv):
        if arg == "--stdio":
            mode = "stdio"
        elif arg == "--mcp":
            mode = "mcp-http"
        elif arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    if mode == "stdio":
        # MCP over stdio — for Claude Code local config
        print("Receipted MCP server (stdio)", file=sys.stderr)
        mcp.run(transport="stdio")

    elif mode == "mcp-http":
        # MCP over streamable HTTP — for Messages API connector
        print(f"Receipted MCP server (HTTP) on port {port}", file=sys.stderr)
        os.environ["FASTMCP_PORT"] = str(port)
        mcp.run(transport="streamable-http")

    else:
        # HTTP face — for iOS app, web clients, curl
        # Also start MCP stdio listener in background for local Claude Code
        ServerClass, HandlerClass = _build_http_app()
        server = ServerClass(("0.0.0.0", port), HandlerClass)

        print(f"Receipted gateway on http://localhost:{port}")
        print(f"  HTTP: POST /threads/analyze, GET /health, GET /")
        print(f"  MCP:  use --stdio for Claude Code, --mcp for HTTP MCP")
        print()

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
