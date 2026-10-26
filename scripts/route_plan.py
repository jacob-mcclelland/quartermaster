"""Route a digested task. Claude's first action on any turn that involves work.

Reads a JSON digest from stdin. The digest holds FACTS about the work; Jev
supplies the judgement; the caller states facts and does not choose the
destination; this prints the routing decision.

  {
    "goal": "one sentence: the outcome wanted",
    "files": ["paths that will change, or sources to consult"],
    "change": "what concretely changes",
    "done_check": "command or observable that proves completion",
    "depends_on_conversation": "the specific facts needed from this
                                conversation, or 'nothing'",
    "out_of_scope": "what must not be touched",
    "audience": "who sees the result: this machine / a client / the public",
    "reversibility": "how it would be undone",
    "pieces": ["optional: independent sub-pieces"]
  }

Characterising words (simple, easy, complex, risky, just, quick...) are
rejected: the author of the digest must not answer their own question.

Usage:
  python scripts/route_plan.py < digest.json
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev.client import LOG_DIR  # noqa: E402
from jev.plan_router import route, validate  # noqa: E402

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _valid_session_id(value: object) -> str | None:
    return value if isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) else None


def _prompt_path(session_id: str) -> Path:
    return LOG_DIR / f".last-prompt-{session_id}.json"


def _marker_path(session_id: str) -> Path:
    return LOG_DIR / f".route-plan-turn-{session_id}.json"


def _current_session_path() -> Path:
    return LOG_DIR / ".current-session.json"


# This runs as a plain Bash command, so it gets no hook payload carrying
# session_id. Order: --session, then CLAUDE_CODE_SESSION_ID (set by Claude Code
# in every Bash call; equals the hook payload's session_id), then the
# .current-session.json pointer route_hook writes. The pointer is a last resort
# for runs outside Claude Code: it is global, so if session B submits a prompt
# between session A's prompt and A's route_plan call, it names B, which is the
# same cross-session race the per-session markers exist to remove.
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


def write_marker(session_id: str | None, digest: dict, result: dict):
    """Write this route's turn marker; return the marker or None if unavailable."""
    session_id = _valid_session_id(session_id)
    if not session_id:
        return None
    try:
        prompt = json.loads(_prompt_path(session_id).read_text(encoding="utf-8"))["prompt"]
        turn = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        marker = {
            "turn": turn,
            "worker": result["worker"],
            "files": list(digest.get("files", [])),
            "veto": False,
            "session_id": session_id,
            "routed_at": datetime.now(timezone.utc).isoformat(),
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _marker_path(session_id).write_text(json.dumps(marker), encoding="utf-8")
        return marker
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    try:
        digest = json.load(sys.stdin)
    except Exception as e:  # noqa: BLE001
        print(f"Could not read a JSON digest from stdin: {e}")
        return 2

    problems = validate(digest)
    if problems:
        print("DIGEST REJECTED. Fix and resend:")
        for p in problems:
            print(f"  - {p}")
        return 2

    try:
        r = route(digest)
    except Exception as e:  # noqa: BLE001
        print(f"Router unavailable ({e}). Use judgement; default to delegating "
              "portable work.")
        return 1

    # Mark this turn as routed, so plan_gate stays quiet.
    write_marker(resolve_session(), digest, r)

    from jev.config import load as load_config
    names = load_config()["roles"]
    print(f"ROUTE: {names[r['worker']]['label']}"
          + (f" at {r['effort']} effort" if r["worker"] in
             ("cheap_worker", "long_context_worker") else ""))
    for reason in r["reasons"]:
        print(f"  because: {reason}")
    if r["after"]:
        print(f"  after:   {r['after']}")
    if r.get("review_fallback", {}).get("on"):
        print("  review:  Codex review fallback ON "
              f"({r['review_fallback']['reason']}): independent reviews go to "
              f"{names['fresh_reviewer']['label']}; cheap-worker implementation "
              "continues")
    if r["fanout"]:
        print("  shape:   pieces split cleanly: fan out to parallel workers")
    print("  answers: " + ", ".join(f"{k}={v}" for k, v in r["answers"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
