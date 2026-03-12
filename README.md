# Drive Coding

**Vibe Coding, but on the go.** Talk to Claude Code from your phone while you drive, walk, or ride.

Drive Coding is a walkie-talkie voice bridge between your phone and a Claude Code session running on your computer. Record a voice message on your phone, it gets transcribed by Whisper, Claude Code processes it, and you hear the response spoken back via Kokoro TTS — all running locally on your own hardware over Tailscale VPN.

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
  | Claude Code reads via /loop
  v
/tmp/claude_voice_outbox.txt
  |
  | Phone polls GET /outbox
  v
Phone (Safari)
  |
  | Kokoro TTS (92MB, runs in browser)
  v
You hear the response
```

## Requirements

- **Mac** with Apple Silicon (for mlx-whisper)
- **Tailscale** on both your Mac and phone ([download](https://tailscale.com/download))
- **Python 3.11+** with dependencies: `pip install starlette uvicorn mlx-whisper`
- **ffmpeg**: `brew install ffmpeg`
- **Claude Code** running on your Mac

## Quick Start

```bash
# 1. Install dependencies
pip install starlette uvicorn mlx-whisper
brew install ffmpeg

# 2. Start the server
python server.py

# 3. Enable HTTPS via Tailscale (required for mic access on iOS Safari)
tailscale serve --bg 8767

# 4. Scan the QR code from your phone, or visit:
#    https://<your-tailscale-hostname>/

# 5. In Claude Code, set up a /loop to process voice messages:
#    /loop 1m Check /tmp/claude_voice_inbox.jsonl for new entries...
```

## Usage

1. **Tap the big button** to start recording
2. **Tap again** to stop and send
3. **Wait** for Claude Code to process (button pulses blue)
4. **Tap the green button** to hear the response
5. **Barge in** — tap during playback to interrupt and record a new message

First time playing audio downloads the Kokoro TTS model (~92MB). It's cached after that.

## Voice Engine

Drive Coding uses [Kokoro TTS](https://huggingface.co/hexgrad/kokoro-82m) running entirely in your browser via ONNX/WebAssembly. No cloud TTS APIs. Falls back to browser speechSynthesis if Kokoro can't load.

## Architecture

Two files. That's it.

| File | What |
|------|------|
| `server.py` | Starlette HTTP server. Receives audio, transcribes with Whisper, serves the page, polls for Claude's response. |
| `page.html` | Single-page app. Walkie-talkie UI, MediaRecorder for audio capture, Kokoro TTS for playback. |

Communication happens through two temp files:
- `/tmp/claude_voice_inbox.jsonl` — phone → Claude Code
- `/tmp/claude_voice_outbox.txt` — Claude Code → phone

## Security

All traffic stays on your Tailscale mesh. No public URLs. No authentication needed because Tailscale handles identity. Your voice audio is transcribed locally by mlx-whisper on your Mac — it never hits an external API.

## License

MIT
