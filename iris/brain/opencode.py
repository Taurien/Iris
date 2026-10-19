import asyncio
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_NAME = "iris"

# NOTE: `--agent iris` points at the restricted agent defined in
# iris/opencode.json (deny bash/edit/write/read/glob/grep/list/patch/
# webfetch/websearch/task/skill/lsp, only memory-service MCP enabled),
# which also pins the model to a working NVIDIA-hosted one (NVIDIA_API_KEY
# in .env) — opencode's free-tier models reject headless `opencode run`
# with a 403, and most of NVIDIA's bigger catalog models 404 ("Function
# ... Not found for account ...", an entitlement gate on NVIDIA's side, not
# an opencode bug) until explicitly requested/approved on that account;
# `nvidia/openai/gpt-oss-20b` is the one confirmed working as of 2026-10-19.
#
# `--format json` is NOT a single JSON object — it's NDJSON, one JSON event
# per line (step_start/text/step_finish/...). Confirmed live: every event
# carries `sessionID` at the top level; the actual reply text lives in
# `part.text` on `type: "text"` events (can be more than one for streamed
# chunks); token cost lives in `part.cost` on `type: "step_finish"` events.


async def ask(text: str, session_id: str | None) -> tuple[str, str | None, float | None]:
    argv = [
        "opencode",
        "run",
        text,
        "--format",
        "json",
        "--agent",
        AGENT_NAME,
        "--dir",
        str(PROJECT_ROOT),
    ]
    model = os.environ.get("IRIS_MODEL", "").strip()
    if model:
        argv += ["--model", model]
    if session_id:
        argv += ["--session", session_id, "--continue"]

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"opencode run failed ({proc.returncode}): {stderr.decode(errors='replace')[:500]}")

    raw = stdout.decode()
    text_parts: list[str] = []
    new_session_id = session_id
    cost: float | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        new_session_id = event.get("sessionID") or new_session_id
        part = event.get("part") or {}
        if event.get("type") == "text" and part.get("text"):
            text_parts.append(part["text"])
        elif event.get("type") == "step_finish" and part.get("cost") is not None:
            cost = part["cost"]

    reply = "".join(text_parts).strip() or raw.strip()
    return reply, new_session_id, cost
