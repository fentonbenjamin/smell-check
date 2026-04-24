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

from .chamber import process_through_chamber, verify_custody, measure_chamber
from .projections import project_smell_check, project_consumer, project_pro

PORT = int(os.environ.get("GATEWAY_PORT", "8800"))

# ---------------------------------------------------------------------------
# MCP server — the tool face
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Smell Check",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8800")),
    transport_security={
        "enable_dns_rebinding_protection": True,
        "allowed_hosts": [
            "127.0.0.1:*", "localhost:*", "[::1]:*",
            "smell-check.fly.dev", "smell-check.fly.dev:*",
            "*.fly.dev", "*.fly.dev:*",
        ],
        "allowed_origins": [
            "http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*",
            "https://smell-check.fly.dev", "https://*.fly.dev",
        ],
    },
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
        # Layer 2: Receipt status
        "receipt_status": {
            "wall": verification["wall_state"],
            "valid": verification["valid"],
            "checks": verification["checks_performed"],
            "perception_mode": verification["perception_mode"],
            "execution_class": verification["execution_class"],
        },
        # Legacy key (backward compat)
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
# Consumer page
# ---------------------------------------------------------------------------

_CONSUMER_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smell Check</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#fafafa;color:#1a1a1a;max-width:480px;margin:0 auto;padding:16px;min-height:100vh;display:flex;flex-direction:column}
h1{font-size:22px;font-weight:600;margin-bottom:4px}
.sub{font-size:14px;color:#666;margin-bottom:20px;line-height:1.4}
textarea{width:100%;min-height:120px;padding:12px;border:1px solid #ddd;border-radius:10px;font-size:16px;font-family:inherit;resize:vertical;outline:none}
textarea:focus{border-color:#007aff}
button{width:100%;padding:14px;margin-top:12px;background:#007aff;color:#fff;border:none;border-radius:10px;font-size:16px;font-weight:600;cursor:pointer}
button:active{background:#0056b3}
button:disabled{background:#ccc;cursor:not-allowed}
.results{margin-top:24px;flex:1}
.section{margin-bottom:16px}
.section h2{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#888;margin-bottom:6px}
.card{background:#fff;border:1px solid #e8e8e8;border-radius:10px;padding:12px 14px;margin-bottom:6px;font-size:15px;line-height:1.5}
.card.finding{border-left:3px solid #ff3b30}
.card.stable{border-left:3px solid #34c759}
.card.open{border-left:3px solid #ff9500}
.because{font-size:13px;color:#666;margin-top:4px}
.gauge{display:flex;align-items:center;gap:10px;padding:12px 14px;border-radius:10px;margin-bottom:16px;font-size:14px}
.gauge.green{background:#e8f5e9;border:1px solid #34c759}
.gauge.red{background:#fce4ec;border:1px solid #ff3b30}
.dot{width:14px;height:14px;border-radius:50%;flex-shrink:0}
.gauge.green .dot{background:#34c759;box-shadow:0 0 6px #34c75980}
.gauge.red .dot{background:#ff3b30;box-shadow:0 0 6px #ff3b3080}
.gauge-text{font-size:13px;color:#333}
.empty{text-align:center;color:#999;padding:40px 20px;font-size:15px;line-height:1.5}
.loading{text-align:center;padding:20px;color:#999}
</style>
</head>
<body>
<h1>Smell Check</h1>
<p class="sub">Paste a thread or code snippet.</p>
<textarea id="input" placeholder="Paste here..."></textarea>
<button id="go" onclick="run()">Smell Check</button>
<div class="results" id="results">
<div class="empty">Paste something and hit the button.</div>
</div>
<script>
async function run(){
const t=document.getElementById('input').value.trim();
if(!t)return;
const b=document.getElementById('go'),r=document.getElementById('results');
b.disabled=true;b.textContent='Checking...';
r.innerHTML='<div class="loading">Smelling...</div>';
try{
const resp=await fetch('/threads/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})});
const d=await resp.json();
if(d.error){r.innerHTML='<div class="empty">'+d.error+'</div>';return}
let h='';
const v=d.verification||{};
if(v.valid){h+='<div class="gauge green"><div class="dot"></div><div class="gauge-text">Verified. Wall held. '+v.checks+' checks passed.</div></div>'}
else{h+='<div class="gauge red"><div class="dot"></div><div class="gauge-text">Custody broken.</div></div>'}
h+=sec('Findings',d.findings||[],'finding');
h+=sec('Stable',d.stable_points||[],'stable');
h+=sec('Open Questions',d.open_questions||[],'open');
if(!d.findings?.length&&!d.stable_points?.length&&!d.open_questions?.length)h+='<div class="empty">Nothing detected. Try more text.</div>';
r.innerHTML=h;
}catch(e){r.innerHTML='<div class="empty">Error.</div>'}
finally{b.disabled=false;b.textContent='Smell Check'}
}
function sec(title,items,cls){
if(!items.length)return'';
let h='<div class="section"><h2>'+title+'</h2>';
for(const i of items){h+='<div class="card '+cls+'">'+esc(i.judgment);if(i.because)h+='<div class="because">'+esc(i.because)+'</div>';h+='</div>'}
return h+'</div>'}
function esc(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML}
document.getElementById('input').addEventListener('keydown',e=>{if(e.key==='Enter'&&e.metaKey)run()});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _build_combined_app(port: int):
    """Build a combined ASGI app: MCP on /mcp, HTTP API on everything else."""
    from contextlib import asynccontextmanager
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, HTMLResponse
    from starlette.routing import Route, Mount

    # Get the MCP ASGI app from FastMCP
    mcp_app = mcp.streamable_http_app()

    async def health(request: Request):
        m = measure_chamber()
        return JSONResponse({"status": "ok", "chamber_hash": m["chamber_hash"][:16] + "..."})

    async def analyze(request: Request):
        body = await request.json()
        text = body.get("text", "")
        if not text:
            return JSONResponse({"error": "provide 'text'"}, status_code=400)
        topic = body.get("topic", "default")
        try:
            result = smell_check(text, topic=topic)
            return JSONResponse(result, media_type="application/json")
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    async def verify(request: Request):
        body = await request.json()
        if "custody_record" in body and "boundary_attestation" not in body:
            body = body["custody_record"]
        # Fail closed: missing required fields = invalid, not a server error
        required = ["boundary_attestation", "authoritative_output"]
        for field in required:
            if field not in body:
                return JSONResponse({
                    "valid": False,
                    "wall_state": "broken",
                    "errors": [f"missing required field: {field}"],
                    "checks_performed": 0,
                    "perception_mode": "unknown",
                    "execution_class": "unknown",
                    "security_mode": "unknown",
                })
        try:
            result = verify_custody(body)
            return JSONResponse(result)
        except Exception as e:
            return JSONResponse({
                "valid": False,
                "wall_state": "broken",
                "errors": [str(e)],
                "checks_performed": 0,
                "perception_mode": "unknown",
                "execution_class": "unknown",
                "security_mode": "unknown",
            })

    async def home(request: Request):
        return HTMLResponse(_CONSUMER_PAGE)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/threads/analyze", analyze, methods=["POST"]),
            Route("/threads/verify", verify, methods=["POST"]),
            Route("/", home, methods=["GET"]),
            Mount("", app=mcp_app),
        ],
        lifespan=lifespan,
    )

    return app


def main():
    mode = "http"
    port = PORT

    for i, arg in enumerate(sys.argv):
        if arg == "--stdio":
            mode = "stdio"
        elif arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    if mode == "stdio":
        # MCP over stdio — for Claude Code local config
        print("Smell Check MCP server (stdio)", file=sys.stderr)
        mcp.run(transport="stdio")

    else:
        # Combined server: HTTP API + MCP streamable HTTP on same port
        # HTTP: GET /, GET /health, POST /threads/analyze, POST /threads/verify
        # MCP:  /mcp (streamable HTTP MCP endpoint)
        import uvicorn
        app = _build_combined_app(port)

        print(f"Smell Check on http://localhost:{port}")
        print(f"  HTTP: GET /, POST /threads/analyze, POST /threads/verify")
        print(f"  MCP:  http://localhost:{port}/mcp")
        print(f"  stdio: use --stdio for Claude Code local")
        print()

        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
