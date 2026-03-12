# Drive Coding

**Vibe Coding, but on the go.** Talk to Claude Code from your phone while you drive, walk, or ride.

Drive Coding is an MCP server that gives Claude Code voice I/O from your phone. Record a voice message, it gets transcribed by Whisper, Claude Code processes it via MCP tools, and you hear the response spoken back via Kokoro TTS — all running locally on your own hardware over Tailscale VPN.

No cloud APIs for voice. No subscriptions. Your voice never leaves your network.

## How It Works

```
Phone (Safari)
  |
  | Record voice → POST /inbox-audio
  v
Server (your Mac)
  |
  | ffmpeg → wav → mlx-whisper STT
  v
/tmp/claude_voice_inbox.jsonl
  |
  | Claude Code ← MCP tools (check_voice_inbox, send_voice_response)
  v
/tmp/claude_voice_outbox.txt
  |
  | Phone polls GET /outbox
  v
Server (your Mac)
  |
  | Kokoro TTS (server-side) → WAV audio
  v
Phone (Safari)
  |
  | Plays audio
  v
You hear the response
```

## Quick Start

```bash
# 1. Clone
git clone https://github.com/ekras-doloop/drive-coding.git
cd drive-coding

# 2. Install dependencies
pip install starlette uvicorn mlx-whisper kokoro-onnx soundfile
brew install ffmpeg

# 3. Download Kokoro model files (~337MB total, one-time)
wget -P /tmp https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
wget -P /tmp https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

# 4. Add to Claude Code (see MCP Config below)

# 5. Enable HTTPS via Tailscale (required for mic access on iOS Safari)
tailscale serve --bg 8767

# 6. Open https://<your-tailscale-hostname>/ on your phone
```

## MCP Config

Add to your `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "drive-coding": {
      "command": "python3",
      "args": ["/path/to/drive-coding/mcp_server.py"]
    }
  }
}
```

Then restart Claude Code. It will have three new tools:

| Tool | What it does |
|------|-------------|
| `check_voice_inbox` | Returns new voice messages from your phone. Marks them as processed. |
| `send_voice_response` | Sends a text response that gets spoken to you via Kokoro TTS. |
| `get_voice_status` | Shows phone URL, Kokoro status, pending message count. |

Claude Code can check for messages on its own, or you can ask it: *"check my voice messages"*.

## Usage

1. **Tap the big button** to start recording
2. **Tap again** to stop and send
3. **Wait** for Claude Code to process (button pulses blue)
4. **Tap the green button** to hear the response
5. **Barge in** — tap during playback to interrupt and record a new message

## Requirements

- **Mac** with Apple Silicon (for mlx-whisper)
- **Tailscale** on both your Mac and phone ([download](https://tailscale.com/download))
- **Python 3.11+**
- **ffmpeg**: `brew install ffmpeg`
- **Claude Code**

## Voice Engine

[Kokoro TTS](https://huggingface.co/hexgrad/kokoro-82m) runs server-side via `kokoro-onnx`. Your Mac generates the audio, sends WAV to the phone, which just plays it. Falls back to browser speechSynthesis if Kokoro model files aren't present.

## Architecture

Three files.

| File | What |
|------|------|
| `mcp_server.py` | MCP server (stdio) + HTTP server (background thread). The single entry point. |
| `server.py` | Standalone HTTP server. Use this if you prefer `/loop` over MCP. |
| `page.html` | Phone UI. Walkie-talkie button, MediaRecorder, audio playback. |

Communication between phone and Claude Code happens through two temp files:
- `/tmp/claude_voice_inbox.jsonl` — phone → Claude Code
- `/tmp/claude_voice_outbox.txt` — Claude Code → phone

## Security

All traffic stays on your Tailscale mesh. No public URLs. No authentication needed — Tailscale handles identity. Voice audio is transcribed locally by mlx-whisper. It never hits an external API.

## License

MIT
