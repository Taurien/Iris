import json
import time
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "sessions.json"
# Per-backend turn limit before starting a fresh session. claude-code stays
# fast on long sessions (prompt caching — measured ~4s even reused), but
# antigravity has no equivalent: measured 13 turns already costing 40-47s
# per reply (vs. ~4-10s fresh), so it needs to recycle much sooner.
ROTATE_AFTER_TURNS = {"antigravity": 8, "claude-code": 40, "opencode": 40}
DEFAULT_ROTATE_AFTER_TURNS = 20
ROTATE_AFTER_SECONDS = 24 * 60 * 60

_EMPTY_BACKEND = {"session_id": None, "created_at": None, "turn_count": 0}


def _load() -> dict:
    """{"claude-code": {...}, "opencode": {...}, "antigravity": {...}} — one
    independent session per backend, since --resume/--session/--conversation
    ids aren't portable across the 3 CLIs. Switching backends (manually or
    via rotation on failure) starts fresh for that backend, not for all of
    them."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def _should_rotate(backend_name: str, backend_state: dict) -> bool:
    if not backend_state.get("session_id"):
        return False
    limit = ROTATE_AFTER_TURNS.get(backend_name, DEFAULT_ROTATE_AFTER_TURNS)
    if backend_state.get("turn_count", 0) >= limit:
        return True
    created_at = backend_state.get("created_at")
    if created_at and (time.time() - created_at) >= ROTATE_AFTER_SECONDS:
        return True
    return False


def get_current_session_id(backend_name: str) -> str | None:
    """Returns the session id to resume for this specific backend, or None
    to mint a fresh one."""
    state = _load()
    backend_state = state.get(backend_name, _EMPTY_BACKEND)
    if _should_rotate(backend_name, backend_state):
        return None
    return backend_state.get("session_id")


def record_turn(backend_name: str, session_id: str) -> None:
    """Persist the session id this backend returned after a turn."""
    state = _load()
    backend_state = state.get(backend_name, {})
    if backend_state.get("session_id") != session_id:
        backend_state = {"session_id": session_id, "created_at": time.time(), "turn_count": 1}
    else:
        backend_state["turn_count"] = backend_state.get("turn_count", 0) + 1
    state[backend_name] = backend_state
    _save(state)
