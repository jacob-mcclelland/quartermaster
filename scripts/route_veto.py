"""Record an explicit override for a routed implementation turn."""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev.client import LOG_DIR  # noqa: E402

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _valid_session_id(value: object) -> str | None:
    return value if isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) else None


def _prompt_path(session_id: str) -> Path:
    return LOG_DIR / f".last-prompt-{session_id}.json"


def _marker_path(session_id: str) -> Path:
    return LOG_DIR / f".route-plan-turn-{session_id}.json"


def _veto_log_path() -> Path:
    return LOG_DIR / "vetoes.jsonl"


def _current_session_path() -> Path:
    return LOG_DIR / ".current-session.json"


# Same order as route_plan.resolve_session, for the same reason: --session,
# then CLAUDE_CODE_SESSION_ID, then the global pointer only as a last resort,
# because the pointer can name whichever session submitted a prompt last.
def resolve_session(args: list[str] | None = None, environ=None) -> str | None:
    args = sys.argv[1:] if args is None else args
    environ = os.environ if environ is None else environ
    explicit = None
    for index, arg in enumerate(args[:-1]):
        if arg == "--session":
            explicit = _valid_session_id(args[index + 1])
            break

    if explicit:
        return explicit
    session_id = _valid_session_id(environ.get("CLAUDE_CODE_SESSION_ID"))
    if session_id:
        return session_id
    try:
        session_id = json.loads(_current_session_path().read_text(encoding="utf-8")).get("session_id")
        return _valid_session_id(session_id)
    except Exception:  # noqa: BLE001
        return None


def _remove_session_argument(args: list[str]) -> tuple[list[str], list[str]]:
    remaining = []
    explicit = []
    index = 0
    while index < len(args):
        if args[index] == "--session" and index + 1 < len(args) and not explicit:
            explicit = ["--session", args[index + 1]]
            index += 2
        else:
            remaining.append(args[index])
            index += 1
    return remaining, explicit


def main() -> int:
    args, session_args = _remove_session_argument(sys.argv[1:])
    if len(args) != 1 or len(args[0].strip()) < 20:
        print("A specific veto reason of at least 20 characters is required.", file=sys.stderr)
        return 2
    reason = args[0].strip()
    session_id = resolve_session(session_args)
    if not session_id:
        print("Could not resolve a Claude Code session id.", file=sys.stderr)
        return 1
    prompt_state = _prompt_path(session_id)
    routed_state = _marker_path(session_id)
    try:
        prompt = json.loads(prompt_state.read_text(encoding="utf-8"))["prompt"]
        turn = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        marker = json.loads(routed_state.read_text(encoding="utf-8"))
        if marker.get("turn") != turn:
            raise ValueError("the route marker does not match the current turn")
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "turn": turn,
            "session_id": session_id,
            "worker": marker.get("worker"),
            "files": marker.get("files", []),
            "reason": reason,
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _veto_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        marker["veto"] = True
        routed_state.write_text(json.dumps(marker), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"Could not record route veto: {e}", file=sys.stderr)
        return 1
    print(f"Route veto recorded for turn {turn}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
