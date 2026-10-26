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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from jev.client import LOG_DIR  # noqa: E402
except Exception:  # noqa: BLE001
    LOG_DIR = None

try:
    from hooks import review_gate_hook  # noqa: E402
except Exception:  # noqa: BLE001
    review_gate_hook = None

MIN_CHARS = 8
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_NOTIFICATION_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)


def _valid_session_id(value: object) -> str | None:
    return value if isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) else None


def _prompt_path(session_id: str) -> Path:
    return LOG_DIR / f".last-prompt-{session_id}.json"


def _current_session_path() -> Path:
    return LOG_DIR / ".current-session.json"


def handle_notifications(prompt: str, session_id: str, log_dir: Path,
                        assess) -> str | None:
    """Judge completed agents that were recorded for this session."""
    if review_gate_hook is None:
        return None
    try:
        session_id = review_gate_hook._valid_session_id(session_id)
        if not session_id:
            return None
        log_dir = Path(log_dir)
        pending_path = log_dir / f".gate-pending-{session_id}.json"
        if not pending_path.exists():
            return None
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        if not isinstance(pending, dict):
            return None

        contexts = []
        for block in _NOTIFICATION_RE.finditer(prompt):
            content = block.group(1)
            task_match = re.search(r"<task-id>(.*?)</task-id>", content, re.DOTALL)
            if not task_match:
                continue
            result_match = re.search(r"<result>(.*?)</result>", content, re.DOTALL)
            agent_id = task_match.group(1).strip()
            saved = pending.get(agent_id)
            if not isinstance(saved, dict):
                continue
            result = result_match.group(1).strip() if result_match else ""
            summary = review_gate_hook.worker_summary(str(saved.get("asked") or ""), result)
            verdict = assess(summary)
            reasons = verdict.get("reasons", []) if verdict else []
            review_gate_hook._append_verdict(log_dir, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "session_id": session_id,
                "tool_use_id": saved.get("tool_use_id"),
                "agent_id": agent_id,
                "status": "review" if verdict else "pass",
                "reasons": reasons,
            })
            pending.pop(agent_id, None)
            pending_path.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
            if verdict:
                contexts.append(review_gate_hook.review_context(verdict))
        return "\n\n".join(contexts) or None
    except Exception:  # noqa: BLE001
        return None


def _router_reminder() -> str:
    return (
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
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0

        prompt = (payload.get("prompt") or "").strip()
        session_id = _valid_session_id(payload.get("session_id"))
        has_notification = "<task-notification>" in prompt
        if LOG_DIR is not None and session_id and not has_notification:
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                _prompt_path(session_id).write_text(
                    json.dumps({"prompt": prompt}), encoding="utf-8")
                _current_session_path().write_text(
                    json.dumps({"session_id": session_id}), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass

        additions = []
        if len(prompt) >= MIN_CHARS and not prompt.startswith("/"):
            additions.append(_router_reminder())
        if (has_notification and LOG_DIR is not None and session_id
                and review_gate_hook is not None):
            try:
                from jev.review_gate import assess_output

                context = handle_notifications(
                    prompt, session_id, LOG_DIR, assess_output,
                )
                if context:
                    additions.append(context)
            except Exception:  # noqa: BLE001
                pass
        if additions:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "\n\n".join(additions),
                },
            }, ensure_ascii=True))
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
