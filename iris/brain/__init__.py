import logging
import os
import time

from .. import sessions, status
from . import antigravity, claude_code, classify, opencode

log = logging.getLogger("iris.brain")

_BACKENDS = {
    "claude-code": claude_code,
    "opencode": opencode,
    "antigravity": antigravity,
}

# Rotation order by message type (see classify.py). "antigravity" = Google
# models via the user's Google AI Pro plan — default for general chat since
# that plan has the most headroom. Code-related messages go to Claude first.
# Whichever backend is first in the chain, the others are the fallback if it
# fails (rate limit, down, etc.) — each backend keeps its own session, so a
# fallback starts fresh for that backend rather than guessing at a foreign
# session id (see sessions.py).
_GENERAL_CHAIN = ["antigravity", "claude-code", "opencode"]
_CODE_CHAIN = ["claude-code", "opencode", "antigravity"]

_SORRY = (
    "Los tres cerebros fallaron ahora mismo (puede ser límite de uso en los tres a la "
    "vez, poco probable pero pasa). Probá de nuevo en un rato."
)


def _chain_for(text: str) -> list[str]:
    forced = os.environ.get("IRIS_BRAIN", "auto").strip()
    if forced != "auto":
        return [forced]
    return _CODE_CHAIN if classify.is_code_related(text) else _GENERAL_CHAIN


async def ask(text: str, channel: str) -> tuple[str, str | None]:
    """Send a message to the brain, rotating across backends on failure.
    Always returns a reply (never raises) — if every backend in the chain
    fails, returns an apology instead of crashing the caller. The second
    element of the return tuple is the backend that actually answered, or
    None if every backend in the chain failed."""
    chain = _chain_for(text)
    tagged_text = f"[{channel}] {text}"

    for i, backend_name in enumerate(chain):
        backend = _BACKENDS[backend_name]
        status.set_state("thinking", channel, detail=backend_name)
        session_id = sessions.get_current_session_id(backend_name)
        log.info("trying backend %s (session=%s, new_session=%s)", backend_name, session_id, session_id is None)
        started = time.monotonic()
        try:
            reply, new_session_id, cost = await backend.ask(tagged_text, session_id)
        except Exception as exc:
            log.warning("backend %s failed after %.1fs (%s), trying next in chain", backend_name, time.monotonic() - started, exc)
            continue

        elapsed = time.monotonic() - started
        if new_session_id:
            sessions.record_turn(backend_name, new_session_id)
        log.info("backend %s answered in %.1fs (cost=%s)", backend_name, elapsed, f"${cost:.4f}" if cost is not None else "n/a")
        if i > 0:
            log.info("served by fallback backend: %s (chain: %s)", backend_name, chain)
        return reply, backend_name

    log.error("all backends failed for this turn (chain: %s)", chain)
    status.set_state("error", channel, detail="|".join(chain))
    return _SORRY, None
