You are in Drive Coding mode. The user is talking to you from their phone via a walkie-talkie voice interface.

The user invoked `/drive` with these arguments: $ARGUMENTS

## If arguments are empty or "start":
1. Call `get_voice_status` to confirm the server is running
2. Call `check_voice_inbox` for any pending messages
3. Set up a polling loop: `/loop 1m check_voice_inbox — if there are new messages, process them and use send_voice_response with a concise 2-4 sentence reply. If no new messages, do nothing and say nothing.`
4. Tell the user Drive Coding is active and remind them to use `/drive stop` to turn off polling.

## If arguments are "stop":
1. Use CronDelete to cancel the active Drive Coding polling job
2. Confirm the loop is stopped. The MCP server and phone page stay up — they can still type "check voice" manually or restart with `/drive start`.

## If arguments are "check":
1. Call `check_voice_inbox` once. If there are messages, process them and use `send_voice_response`. No loop.

## MCP Tools available:
- `check_voice_inbox` — returns new voice messages, marks them processed
- `send_voice_response` — sends text to be spoken via Kokoro TTS on the phone
- `get_voice_status` — shows phone URL, Kokoro status, pending count
