"""UserPromptSubmit hook: remind Claude to digest and route. Sends NOTHING to Jev.

A user's prompt is often conversational shorthand ("do it", "fix the three
gaps") whose task lives in earlier turns, so only the orchestrating model knows
what the work actually is. This hook therefore asks Jev nothing. It saves the
prompt (so the turn can be identified) and tells Claude to do the digesting
itself: break the request into facts, then put those facts to Jev through
route_plan.py.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from jev.client import LOG_DIR  # noqa: E402
except Exception:  # noqa: BLE001
    LOG_DIR = None

MIN_CHARS = 8
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _valid_session_id(value: object) -> str | None:
    return value if isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) else None


def _prompt_path(session_id: str) -> Path:
    return LOG_DIR / f".last-prompt-{session_id}.json"


def _current_session_path() -> Path:
    return LOG_DIR / ".current-session.json"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0

    if not isinstance(payload, dict):
        return 0

    prompt = (payload.get("prompt") or "").strip()
    session_id = _valid_session_id(payload.get("session_id"))
    if session_id and not prompt.startswith("<task-notification>"):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            _prompt_path(session_id).write_text(
                json.dumps({"prompt": prompt}), encoding="utf-8")
            _current_session_path().write_text(
                json.dumps({"session_id": session_id}), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    if len(prompt) < MIN_CHARS or prompt.startswith("/"):
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "[router] If this turn involves doing work (not only "
                "answering), digest it FIRST, before any other action: work "
                "out what the task actually is, then pipe a JSON digest of "
                "FACTS (goal, files, change, done_check, "
                "depends_on_conversation, out_of_scope, audience, "
                "reversibility, optional pieces) to "
                f"`python {Path(__file__).resolve().parent.parent / 'scripts' / 'route_plan.py'}`. Never send the user's "
                "raw words to Jev, and never put a judgement (simple, risky, "
                "quick...) in the digest. Jev judges, you state facts. Then "
                "follow the route: dispatch to the named worker, spawn the "
                "consult if one is named, arrange the review it asks for. "
                "The routing decision belongs to the router: the agent must not "
                "decide in advance where work should go or frame the digest "
                "to reach a particular destination. Skip only for pure "
                "conversation or a one-line change."
            ),
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
