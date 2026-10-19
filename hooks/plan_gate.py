"""PreToolUse on Edit/Write: did Claude route this turn before starting work?

Backstop for the digest router. route_hook tells Claude to digest the task and
run route_plan.py first; this checks that it happened. If Claude is about to
edit a file on a turn where no route was taken, it says so once.

Sends nothing to Jev. An earlier version judged the raw prompt plus the first
edit, which was a guess at the work; the digest router replaced that with
Claude stating the work outright.
"""

import hashlib
import json
import posixpath
import re
import sys
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


def _nagged_path(session_id: str) -> Path:
    return LOG_DIR / f".plan-gate-fired-{session_id}.json"

# These names are agent working folders or generated state.
SKIP_PARTS = {"logs", "__pycache__", ".git", "scratchpad", "node_modules", "memory"}
SKIP_SUFFIX = {".jsonl", ".log", ".lock"}


def _turn_of(path: Path) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("turn")
    except Exception:  # noqa: BLE001
        return None


def _normal(path: str) -> str:
    """Forward slashes, no ./ or .. segments, lowercase."""
    return posixpath.normpath(path.replace("\\", "/")).lower()


def _is_absolute(path: str) -> bool:
    return path.startswith("/") or bool(re.match(r"^[a-z]:/", path))


def _matches_routed_file(edited_path: str, routed_files: object,
                         cwd: str | None = None) -> bool:
    """Is the file being edited one the route assigned to a worker?

    An absolute routed path matches only itself. A routed path with a folder
    in it ("src/example.py") matches any edited path ending in that path at a
    folder boundary. A bare file name ("README.md") is too loose to match on
    its own, so it is resolved against the session's working folder and must
    match exactly; without a working folder it matches nothing.
    """
    if not isinstance(routed_files, list):
        return False
    edited = _normal(edited_path)
    for routed in routed_files:
        if not isinstance(routed, str) or not routed.strip():
            continue
        candidate = _normal(routed)
        if _is_absolute(candidate):
            if edited == candidate:
                return True
        elif "/" in candidate:
            if edited == candidate or edited.endswith("/" + candidate):
                return True
        elif (isinstance(cwd, str) and cwd
              and edited == _normal(posixpath.join(cwd.replace("\\", "/"), routed))):
            return True
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0

    if not isinstance(payload, dict):
        return 0
    session_id = _valid_session_id(payload.get("session_id"))
    if not session_id:
        return 0

    prompt_state = _prompt_path(session_id)
    routed_state = _marker_path(session_id)
    nagged_state = _nagged_path(session_id)

    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not path:
        return 0
    p = Path(path)
    if SKIP_PARTS & set(p.parts) or p.suffix in SKIP_SUFFIX:
        return 0

    try:
        prompt = json.loads(prompt_state.read_text(encoding="utf-8"))["prompt"]
    except Exception:  # noqa: BLE001
        return 0
    turn = hashlib.sha256(prompt.encode()).hexdigest()[:16]

    # Routed implementation must be dispatched to the chosen cheap worker.
    # This is deliberately fail-open: a damaged marker must not block edits.
    try:
        marker = json.loads(routed_state.read_text(encoding="utf-8"))
        if (marker.get("turn") == turn
                and marker.get("worker") in {"cheap_worker", "long_context_worker"}
                and marker.get("veto") is not True
                and _matches_routed_file(path, marker.get("files"), payload.get("cwd"))):
            worker = marker["worker"]
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"This work was routed to {worker}; it should be dispatched "
                        "rather than edited inline. To override, run: "
                        f"python {Path(__file__).resolve().parent.parent / 'scripts' / 'route_veto.py'} \"<specific reason>\""
                    ),
                },
            }))
            return 0
    except Exception:  # noqa: BLE001
        pass

    if _turn_of(routed_state) == turn:   # routed: the decision was made
        return 0
    if _turn_of(nagged_state) == turn:   # already said so this turn
        return 0
    try:
        nagged_state.write_text(json.dumps({"turn": turn}), encoding="utf-8")
    except OSError:
        pass

    print(json.dumps({
        "systemMessage": "Jev plan-gate: editing without having routed this turn.",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                "[plan-gate] You are about to edit a file but no route was "
                "taken this turn. Unless this is a one-line change or depends "
                "entirely on this conversation, stop and digest the work into "
                f"facts, then run `python {Path(__file__).resolve().parent.parent / 'scripts' / 'route_plan.py'}`. "
                "Route before editing so the selected worker handles the named files."
            ),
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
