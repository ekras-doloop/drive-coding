#!/usr/bin/env python3
"""
Drive Coding — MCP Server
==========================
Add to your .mcp.json and Claude Code gets voice I/O from your phone.
Runs the HTTP server (for phone) in a background thread and exposes
MCP tools (for Claude Code) over stdio.
"""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

# ─── Config ─────────────────────────────────────────────────
INBOX = Path("/tmp/claude_voice_inbox.jsonl")
OUTBOX = Path("/tmp/claude_voice_outbox.txt")
KOKORO_MODEL = Path("/tmp/kokoro-v1.0.onnx")
KOKORO_VOICES = Path("/tmp/voices-v1.0.bin")
PORT = 8767

# ─── Kokoro TTS (lazy-loaded) ───────────────────────────────
_kokoro = None

def get_kokoro():
    global _kokoro
    if _kokoro is None and KOKORO_MODEL.exists() and KOKORO_VOICES.exists():
        try:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
        except Exception:
            pass
    return _kokoro


# ─── Inbox/Outbox helpers ───────────────────────────────────

def read_unprocessed():
    """Read all unprocessed messages from inbox."""
    if not INBOX.exists():
        return []
    messages = []
    lines = INBOX.read_text().strip().split("\n")
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if not entry.get("processed", False):
                messages.append(entry)
        except json.JSONDecodeError:
            continue
    return messages


def mark_processed(timestamps):
    """Mark messages with given timestamps as processed."""
    if not INBOX.exists():
        return
    lines = INBOX.read_text().strip().split("\n")
    new_lines = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if entry.get("timestamp") in timestamps:
                entry["processed"] = True
            new_lines.append(json.dumps(entry))
        except json.JSONDecodeError:
            new_lines.append(line)
    INBOX.write_text("\n".join(new_lines) + "\n")


def write_outbox(text):
    """Write response to outbox for phone to pick up."""
    OUTBOX.write_text(text)


# ─── HTTP Server (runs in background thread) ────────────────

def start_http_server():
    """Start the Starlette HTTP server for the phone."""
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, Response
    from starlette.routing import Route
    import uvicorn

    async def index(request):
        page_path = Path(__file__).parent / "page.html"
        return HTMLResponse(page_path.read_text())

    async def inbox_post(request):
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return Response(content=json.dumps({"status": "empty"}),
                            media_type="application/json")
        entry = {
            "timestamp": datetime.now().isoformat(),
            "text": text,
            "processed": False,
        }
        with open(INBOX, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return Response(content=json.dumps({"status": "ok"}),
                        media_type="application/json")

    async def inbox_audio(request):
        form = await request.form()
        audio_file = form.get("audio")
        if not audio_file:
            return Response(content=json.dumps({"error": "no audio"}),
                            media_type="application/json", status_code=400)
        audio_bytes = await audio_file.read()
        suffix = ".mp4" if "mp4" in (audio_file.content_type or "") else ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            wav_path = tmp_path + ".wav"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_path, "-ar", "16000", "-ac", "1", wav_path],
                capture_output=True, timeout=10)
            if proc.returncode != 0:
                return Response(content=json.dumps({"error": "ffmpeg failed"}),
                                media_type="application/json", status_code=500)
            text = transcribe_audio(wav_path)
            if text and text.strip():
                entry = {
                    "timestamp": datetime.now().isoformat(),
                    "text": text.strip(),
                    "processed": False,
                }
                with open(INBOX, "a") as f:
                    f.write(json.dumps(entry) + "\n")
                return Response(content=json.dumps({"text": text.strip(), "status": "ok"}),
                                media_type="application/json")
            else:
                return Response(content=json.dumps({"text": "", "status": "no_speech"}),
                                media_type="application/json")
        finally:
            for p in [tmp_path, tmp_path + ".wav"]:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    async def outbox_get(request):
        if OUTBOX.exists() and OUTBOX.stat().st_size > 0:
            content = OUTBOX.read_text().strip()
            if content:
                OUTBOX.unlink()
                return Response(content=json.dumps({"text": content}),
                                media_type="application/json")
        return Response(content=json.dumps({"text": ""}),
                        media_type="application/json")

    async def tts_post(request):
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return Response(content=json.dumps({"error": "no text"}),
                            media_type="application/json", status_code=400)
        kokoro = get_kokoro()
        if not kokoro:
            return Response(content=json.dumps({"error": "kokoro not available"}),
                            media_type="application/json", status_code=503)
        try:
            import soundfile as sf
            samples, sr = kokoro.create(text, voice='af_heart', speed=1.0)
            buf = io.BytesIO()
            sf.write(buf, samples, sr, format='WAV')
            buf.seek(0)
            return Response(content=buf.read(), media_type="audio/wav")
        except Exception as e:
            return Response(content=json.dumps({"error": str(e)}),
                            media_type="application/json", status_code=500)

    async def health(request):
        return Response(content="drive-coding ok", media_type="text/plain")

    app = Starlette(routes=[
        Route("/", index),
        Route("/inbox", inbox_post, methods=["POST"]),
        Route("/inbox-audio", inbox_audio, methods=["POST"]),
        Route("/outbox", outbox_get, methods=["GET"]),
        Route("/tts", tts_post, methods=["POST"]),
        Route("/health", health),
    ])

    # Redirect uvicorn logs to stderr (stdout is MCP stdio)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning",
                log_config=None)


def transcribe_audio(wav_path):
    try:
        import mlx_whisper
        result = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo="mlx-community/whisper-base-mlx",
        )
        return result.get("text", "")
    except ImportError:
        try:
            proc = subprocess.run(
                ["whisper", wav_path, "--model", "base", "--output_format", "txt",
                 "--output_dir", "/tmp"],
                capture_output=True, text=True, timeout=30)
            txt_path = wav_path.rsplit(".", 1)[0] + ".txt"
            if os.path.exists(txt_path):
                text = open(txt_path).read().strip()
                os.unlink(txt_path)
                return text
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return ""


# ─── Tailscale helpers ──────────────────────────────────────

def get_tailscale_url():
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5)
        data = json.loads(result.stdout)
        dns_name = data.get("Self", {}).get("DNSName", "")
        if dns_name:
            return f"https://{dns_name.rstrip('.')}"
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5)
        ip = result.stdout.strip()
        if ip:
            return f"http://{ip}:{PORT}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return f"http://localhost:{PORT}"


# ─── MCP Protocol (stdio JSON-RPC) ─────────────────────────

def send_response(id, result):
    msg = json.dumps({"jsonrpc": "2.0", "id": id, "result": result})
    sys.stdout.write(f"Content-Length: {len(msg)}\r\n\r\n{msg}")
    sys.stdout.flush()


def send_notification(method, params=None):
    msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})
    sys.stdout.write(f"Content-Length: {len(msg)}\r\n\r\n{msg}")
    sys.stdout.flush()


def read_message():
    """Read a JSON-RPC message from stdin (MCP stdio transport)."""
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line:
            return None  # EOF
        line = line.strip()
        if line == "":
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip()] = value.strip()
    content_length = int(headers.get("Content-Length", 0))
    if content_length == 0:
        return None
    body = sys.stdin.read(content_length)
    return json.loads(body)


TOOLS = [
    {
        "name": "check_voice_inbox",
        "description": "Check for new voice messages from the caller's phone. Returns unprocessed messages and marks them as processed. Call this periodically or when prompted.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "send_voice_response",
        "description": "Send a text response that will be spoken to the caller via Kokoro TTS on their phone. Keep responses concise (2-4 sentences) since they'll be spoken aloud.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The response text to speak to the caller",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "get_voice_status",
        "description": "Get the status of Drive Coding: phone URL, whether Kokoro TTS is available, and pending message count.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def handle_tool_call(name, arguments):
    if name == "check_voice_inbox":
        messages = read_unprocessed()
        if not messages:
            return {"content": [{"type": "text", "text": "No new voice messages."}]}
        timestamps = [m["timestamp"] for m in messages]
        texts = []
        for m in messages:
            ts = m["timestamp"].split("T")[1][:8]
            texts.append(f"[{ts}] {m['text']}")
        mark_processed(timestamps)
        combined = "\n".join(texts)
        return {"content": [{"type": "text", "text": f"{len(messages)} new message(s):\n{combined}"}]}

    elif name == "send_voice_response":
        text = arguments.get("text", "").strip()
        if not text:
            return {"content": [{"type": "text", "text": "Error: no text provided"}], "isError": True}
        write_outbox(text)
        return {"content": [{"type": "text", "text": f"Response sent to phone: {text[:80]}..."}]}

    elif name == "get_voice_status":
        url = get_tailscale_url()
        kokoro_ok = KOKORO_MODEL.exists() and KOKORO_VOICES.exists()
        pending = len(read_unprocessed())
        status = (
            f"Drive Coding status:\n"
            f"  Phone URL: {url}\n"
            f"  Kokoro TTS: {'ready' if kokoro_ok else 'not found (install model files)'}\n"
            f"  Pending messages: {pending}\n"
            f"  Inbox: {INBOX}\n"
            f"  Outbox: {OUTBOX}"
        )
        return {"content": [{"type": "text", "text": status}]}

    return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}


def mcp_loop():
    """Main MCP message loop."""
    while True:
        msg = read_message()
        if msg is None:
            break

        method = msg.get("method", "")
        id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            send_response(id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "drive-coding",
                    "version": "1.0.0",
                },
            })

        elif method == "notifications/initialized":
            pass  # Client ack, nothing to do

        elif method == "tools/list":
            send_response(id, {"tools": TOOLS})

        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = handle_tool_call(name, arguments)
            send_response(id, result)

        elif method == "ping":
            send_response(id, {})

        elif id is not None:
            # Unknown method with an ID — respond with error
            send_response(id, {"error": {"code": -32601, "message": f"Unknown method: {method}"}})


# ─── Main ────────────────────────────────────────────────────

if __name__ == "__main__":
    # Start HTTP server in background thread
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    # Print startup info to stderr (stdout is MCP)
    url = get_tailscale_url()
    print(f"Drive Coding MCP server started", file=sys.stderr)
    print(f"  Phone URL: {url}", file=sys.stderr)
    print(f"  Run: tailscale serve --bg {PORT}", file=sys.stderr)

    # Run MCP loop on main thread
    mcp_loop()
