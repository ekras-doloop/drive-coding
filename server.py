#!/usr/bin/env python3
"""
Drive Coding — Talk to Claude Code from your phone
====================================================
Lightweight HTTP server that bridges a web page on your phone to
Claude Code's voice inbox/outbox. Requires Tailscale on both devices.

Architecture:
  Phone (Safari) → MediaRecorder → POST /inbox-audio → Whisper STT
  → /tmp/claude_voice_inbox.jsonl → Claude Code /loop reads it
  → /tmp/claude_voice_outbox.txt → this server → GET /outbox
  → Phone (Safari) → speechSynthesis TTS speaks it

Usage:
  python3 cctg_server.py [--port 8767]
  # Then open https://<tailscale-hostname> on your phone (via tailscale serve)

Security: Tailscale mesh only. No public URLs. No auth layer needed.
"""

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route
import uvicorn

# ─── Config ─────────────────────────────────────────────────
INBOX = Path("/tmp/claude_voice_inbox.jsonl")
OUTBOX = Path("/tmp/claude_voice_outbox.txt")

# ─── Routes ─────────────────────────────────────────────────

async def index(request):
    """Serve the Drive Coding web page."""
    page_path = Path(__file__).parent / "page.html"
    html = page_path.read_text()
    return HTMLResponse(html)


async def inbox_post(request):
    """Receive transcribed text from the phone."""
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
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] inbox: {text}")
    return Response(content=json.dumps({"status": "ok"}),
                    media_type="application/json")


async def inbox_audio(request):
    """Receive audio from phone, transcribe with Whisper, write to inbox."""
    form = await request.form()
    audio_file = form.get("audio")
    if not audio_file:
        return Response(content=json.dumps({"error": "no audio"}),
                        media_type="application/json", status_code=400)

    # Save uploaded audio to temp file
    audio_bytes = await audio_file.read()
    suffix = ".mp4" if "mp4" in (audio_file.content_type or "") else ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        # Convert to wav with ffmpeg (Whisper needs wav/mp3)
        wav_path = tmp_path + ".wav"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_path, "-ar", "16000", "-ac", "1", wav_path],
            capture_output=True, timeout=10)
        if proc.returncode != 0:
            return Response(content=json.dumps({"error": "ffmpeg failed"}),
                            media_type="application/json", status_code=500)

        # Transcribe with mlx-whisper
        text = transcribe_audio(wav_path)
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] whisper: {text}")

        if text and text.strip():
            # Write to inbox
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
        # Cleanup temp files
        for p in [tmp_path, tmp_path + ".wav"]:
            try:
                os.unlink(p)
            except OSError:
                pass


def transcribe_audio(wav_path):
    """Transcribe audio file using mlx-whisper."""
    try:
        import mlx_whisper
        result = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo="mlx-community/whisper-base-mlx",
        )
        return result.get("text", "")
    except ImportError:
        # Fallback: try openai whisper CLI
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


async def outbox_get(request):
    """Poll for Claude Code's response."""
    if OUTBOX.exists() and OUTBOX.stat().st_size > 0:
        content = OUTBOX.read_text().strip()
        if content:
            OUTBOX.unlink()
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] outbox: {content[:80]}...")
            return Response(
                content=json.dumps({"text": content}),
                media_type="application/json")
    return Response(
        content=json.dumps({"text": ""}),
        media_type="application/json")


async def health(request):
    return Response(content="cctg ok", media_type="text/plain")


app = Starlette(routes=[
    Route("/", index),
    Route("/inbox", inbox_post, methods=["POST"]),
    Route("/inbox-audio", inbox_audio, methods=["POST"]),
    Route("/outbox", outbox_get, methods=["GET"]),
    Route("/health", health),
])


# ─── Tailscale IP detection ─────────────────────────────────

def get_tailscale_ip():
    """Get this machine's Tailscale IP."""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5)
        ip = result.stdout.strip()
        if ip:
            return ip
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fallback: look for 100.x.y.z interface
    try:
        import netifaces
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            for addr in addrs.get(netifaces.AF_INET, []):
                ip = addr.get("addr", "")
                if ip.startswith("100."):
                    return ip
    except ImportError:
        pass
    return None


def get_hostname():
    """Get Tailscale MagicDNS hostname if available."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5)
        data = json.loads(result.stdout)
        self_node = data.get("Self", {})
        dns_name = self_node.get("DNSName", "")
        if dns_name:
            return dns_name.rstrip(".")
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return socket.gethostname()


# ─── QR Code (terminal) ─────────────────────────────────────

def print_qr(url):
    """Print a QR code in the terminal. Falls back to just the URL."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        # Try system qrencode
        try:
            subprocess.run(["qrencode", "-t", "ANSIUTF8", url], check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            print(f"  (install 'qrcode' for QR: pip install qrcode)")


# ─── Main ────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drive Coding")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()

    ts_ip = get_tailscale_ip()
    hostname = get_hostname()

    print("=" * 56)
    print("  DRIVE CODING")
    print("  Talk to Claude Code from your phone")
    print("=" * 56)

    if ts_ip:
        url = f"http://{ts_ip}:{args.port}"
        print(f"\n  Tailscale IP:  {ts_ip}")
        print(f"  URL:           {url}")
        if hostname:
            dns_url = f"http://{hostname}:{args.port}"
            print(f"  MagicDNS:      {dns_url}")
        print(f"\n  Scan with your phone:\n")
        print_qr(url)
    else:
        url = f"http://localhost:{args.port}"
        print(f"\n  WARNING: Tailscale not detected!")
        print(f"  Local URL: {url}")
        print(f"  Install Tailscale: https://tailscale.com/download")

    print(f"\n  Claude Code reads: {INBOX}")
    print(f"  Claude Code writes: {OUTBOX}")
    print(f"\n  Set up /loop in Claude Code to process messages.")
    print("=" * 56)

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
