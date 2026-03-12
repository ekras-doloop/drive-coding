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

from mcp.server.fastmcp import FastMCP

# ─── Config ─────────────────────────────────────────────────
INBOX = Path("/tmp/claude_voice_inbox.jsonl")
OUTBOX = Path("/tmp/claude_voice_outbox.txt")
KOKORO_MODEL = Path(os.path.expanduser("~/Library/Caches/kokoro/kokoro-v1.0.onnx"))
KOKORO_VOICES = Path(os.path.expanduser("~/Library/Caches/kokoro/voices-v1.0.bin"))
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
    OUTBOX.write_text(text)


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


# ─── HTTP Server (runs in background thread) ────────────────

def start_http_server():
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


# ─── MCP Server (FastMCP) ───────────────────────────────────

mcp = FastMCP("drive-coding")


@mcp.tool()
def check_voice_inbox() -> str:
    """Check for new voice messages from the caller's phone. Returns unprocessed messages and marks them as processed."""
    messages = read_unprocessed()
    if not messages:
        return "No new voice messages."
    timestamps = [m["timestamp"] for m in messages]
    texts = []
    for m in messages:
        ts = m["timestamp"].split("T")[1][:8]
        texts.append(f"[{ts}] {m['text']}")
    mark_processed(timestamps)
    combined = "\n".join(texts)
    return f"{len(messages)} new message(s):\n{combined}"


@mcp.tool()
def send_voice_response(text: str) -> str:
    """Send a text response that will be spoken to the caller via Kokoro TTS on their phone. Keep responses concise (2-4 sentences) since they'll be spoken aloud."""
    text = text.strip()
    if not text:
        return "Error: no text provided"
    write_outbox(text)
    return f"Response sent to phone: {text[:80]}..."


@mcp.tool()
def get_voice_status() -> str:
    """Get the status of Drive Coding: phone URL, whether Kokoro TTS is available, and pending message count."""
    url = get_tailscale_url()
    kokoro_ok = KOKORO_MODEL.exists() and KOKORO_VOICES.exists()
    pending = len(read_unprocessed())
    return (
        f"Drive Coding status:\n"
        f"  Phone URL: {url}\n"
        f"  Kokoro TTS: {'ready' if kokoro_ok else 'not found (install model files)'}\n"
        f"  Pending messages: {pending}\n"
        f"  Inbox: {INBOX}\n"
        f"  Outbox: {OUTBOX}"
    )


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

    # Run MCP server on main thread (stdio transport)
    mcp.run(transport="stdio")
