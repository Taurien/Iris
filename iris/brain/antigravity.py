import asyncio
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Restricted agent defined in .agents/agents/iris.md: tools: [] +
# commandExecutionPolicy: off structurally removes file/shell tools (empirically
# verified — they stay denied even with --dangerously-skip-permissions, see
# below), with mcpServers scoped to memory-service only.
#
# --dangerously-skip-permissions is required because headless (-p) mode has
# no one to answer the interactive permission prompt agy would otherwise
# show for the memory-service MCP tool, and auto-denies it by default (same
# issue --mode auto would normally sidestep, but it isn't available on this
# account tier). This is safe specifically because tools: [] already removes
# every other tool at the agent-definition level — there is nothing else for
# the flag to unlock. Confirmed empirically: a direct "read .env" request
# was still refused with this flag set.
AGENT_NAME = "iris"

# agy's default model (no --model flag) turned out to be a heavy reasoning
# model — measured 46.8s for a plain "hola" on an existing session. Pinning
# an explicit flash/low-effort model brought the same message down to 4.3s,
# in line with claude-code's latency, while still running under the user's
# Google AI Pro plan (not a separately billed API). IRIS_MODEL overrides
# this if set, but don't use it for that — it's shared with opencode.py,
# which expects a different "provider/model" format.
DEFAULT_MODEL = "gemini-3.8-flash-low"


async def ask(text: str, session_id: str | None) -> tuple[str, str | None, float | None]:
    argv = [
        "agy",
        "-p",
        text,
        "--output-format",
        "json",
        "--sandbox",
        "--agent",
        AGENT_NAME,
        "--dangerously-skip-permissions",
    ]
    model = os.environ.get("IRIS_MODEL", "").strip() or DEFAULT_MODEL
    argv += ["--model", model]
    if session_id:
        argv += ["--continue", "--conversation", session_id]

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"agy -p failed ({proc.returncode}): {stderr.decode(errors='replace')[:500]}")

    raw = stdout.decode()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip(), session_id, None

    reply = data.get("response") or data.get("text") or data.get("result") or raw.strip()
    new_session_id = data.get("conversation_id") or data.get("conversationId") or session_id
    cost = data.get("cost")
    return reply, new_session_id, cost
