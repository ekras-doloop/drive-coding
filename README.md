# Drive Coding

**Vibe Coding, but on the go.** Talk to Claude Code from your phone while you drive, walk, or ride.

Drive Coding is an MCP server that gives Claude Code voice I/O from your phone. Record a voice message, it gets transcribed by Whisper, Claude Code processes it via MCP tools, and you hear the response spoken back via Kokoro TTS.

Everything runs on your own hardware. Your voice travels over your Tailscale VPN, gets transcribed on your Mac, and never touches a cloud API. There is no server you don't own. There is no endpoint you didn't start. The only network involved is the one where every device is already yours.

*Drive Coding was built using Drive Coding. The tool was its own first user — described from a car, built by Claude Code, tested on the same call.*

## How It Works

```
Phone (Safari)
  |
  | Record voice → POST /inbox-audio
  v
Your Mac (Tailscale VPN)
  |
  | ffmpeg → wav → mlx-whisper STT (local, on-device)
  v
/tmp/claude_voice_inbox.jsonl
  |
  | Claude Code ← MCP tools (check_voice_inbox, send_voice_response)
  v
/tmp/claude_voice_outbox.txt
  |
  | Phone polls GET /outbox
  v
Your Mac
  |
  | Kokoro TTS (local, on-device) → WAV audio
  v
Phone plays audio
```

**Nothing leaves your network.** STT is mlx-whisper on your Mac. TTS is Kokoro on your Mac. The HTTP server only binds to your Tailscale mesh. The phone connects over your private VPN. There is no cloud hop.

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

Restart Claude Code. It will have three new tools:

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

## Security

This is the safest voice-to-code setup possible:

- **Tailscale mesh only.** No public URLs. No ports open to the internet. Every device on the network is one you own and authenticated.
- **Local STT.** mlx-whisper runs on your Mac. Your voice audio is never uploaded anywhere.
- **Local TTS.** Kokoro runs on your Mac. Generated speech never leaves your machine.
- **No cloud APIs.** No OpenAI Whisper API. No ElevenLabs. No Google Cloud Speech. Nothing.
- **No auth layer needed.** Tailscale handles identity — if you can reach the URL, you're already on the mesh.

Your voice goes from your phone to your Mac over an encrypted Tailscale tunnel. It gets transcribed into text on your Mac. Claude Code reads the text. The response gets synthesized into audio on your Mac. Your phone plays it. At no point does any audio or text leave your private network.

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

## License

MIT
