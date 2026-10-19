import asyncio
import json
import os
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_CONFIG = PROJECT_ROOT / "config" / "mcp.json"
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.json"


async def ask(text: str, session_id: str | None) -> tuple[str, str | None, float | None]:
    argv = [
        "claude",
        "-p",
        text,
        "--output-format",
        "json",
        "--mcp-config",
        str(MCP_CONFIG),
        "--strict-mcp-config",
        "--settings",
        str(SETTINGS_FILE),
        "--tools",
        "",
        "--allowedTools",
        "mcp__memory-service__*",
    ]
    model = os.environ.get("IRIS_MODEL", "").strip()
    if model:
        argv += ["--model", model]

    if session_id:
        argv += ["--resume", session_id]
        new_session_id = session_id
    else:
        new_session_id = str(uuid.uuid4())
        argv += ["--session-id", new_session_id]

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {stderr.decode(errors='replace')[:500]}")

    data = json.loads(stdout.decode())
    reply = data.get("result", "")
    returned_session_id = data.get("session_id", new_session_id)
    cost = data.get("total_cost_usd")
    return reply, returned_session_id, cost
