"""PostToolUse hook for review-gate decisions.

Background agents are recorded when launched and judged when their completion
notification arrives. Synchronous Agent and Task results are judged here.
Codex dispatches are judged by scripts/dispatch.py. Silent by default. Fails open.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MIN_CHARS = 60
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _valid_session_id(value: object) -> str | None:
    return value if isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) else None


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_verdict(log_dir: Path, entry: dict) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "review-gate-verdicts.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def worker_summary(asked: str, came_back: str) -> str:
    return (f"TASK GIVEN TO THE WORKER:\n{asked[:3000]}\n\n"
            f"WHAT THE WORKER REPORTED:\n{came_back[:4000]}")


def describe(payload: dict) -> str | None:
    """What was asked, and what came back."""
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    resp = payload.get("tool_response")

    if tool in ("Agent", "Task"):
        asked = ti.get("prompt", "")
    else:
        return None

    if not asked or len(asked) < MIN_CHARS:
        return None

    if isinstance(resp, dict):
        came_back = json.dumps(resp, ensure_ascii=False)[:4000]
    else:
        came_back = str(resp or "")[:4000]

    return worker_summary(asked, came_back)


def review_context(verdict: dict) -> str:
    """The instruction shown when a finished result warrants a review."""
    why = "; ".join(verdict["reasons"])
    return (
        f"[review-gate] This finished work wants checking: {why}. "
        f"Send it to {verdict['who']} ({verdict['why']}). "
        "Give the reviewer the diff plus the surrounding code, the spec, and "
        "the real test output; withhold the author's reasoning and which model "
        "wrote it. Then turn every finding into a test before acting on it, "
        "because agreement between models is not proof. If a test could settle "
        "this instead, run the test and skip the review."
    )


def _async_launch(tool: str, response: object) -> tuple[bool, str | None, str]:
    if tool not in ("Agent", "Task"):
        return False, None, ""
    if isinstance(response, dict) and (
            response.get("status") == "async_launched" or response.get("isAsync") is True):
        agent_id = response.get("agentId")
        return True, agent_id if isinstance(agent_id, str) else None, \
            str(response.get("description") or "")
    if isinstance(response, str) and response.startswith("Async agent launched successfully"):
        match = re.search(r"\bagentId:\s*([A-Za-z0-9_-]+)", response)
        return True, match.group(1) if match else None, ""
    return False, None, ""


def _record_pending(payload: dict, log_dir: Path, agent_id: str,
                    description: str) -> None:
    session_id = _valid_session_id(payload.get("session_id"))
    if not session_id:
        return
    path = log_dir / f".gate-pending-{session_id}.json"
    pending = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(pending, dict):
        pending = {}
    ti = payload.get("tool_input") or {}
    pending[agent_id] = {
        "asked": str(ti.get("prompt") or "")[:3000],
        "description": description or str(ti.get("description") or ""),
        "tool_use_id": payload.get("tool_use_id"),
        "ts": _ts(),
    }
    log_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
    _append_verdict(log_dir, {
        "ts": _ts(), "session_id": session_id,
        "tool_use_id": payload.get("tool_use_id"), "agent_id": agent_id,
        "status": "pending",
    })


def handle(payload: dict, log_dir: Path, assess) -> dict | None:
    """Process a PostToolUse event. Any failure stays silent."""
    try:
        tool = payload.get("tool_name", "")
        response = payload.get("tool_response")
        is_launch, agent_id, description = _async_launch(tool, response)
        if is_launch:
            if agent_id:
                _record_pending(payload, Path(log_dir), agent_id, description)
            return None

        summary = describe(payload)
        if not summary:
            return None

        verdict = assess(summary)
        session_id = _valid_session_id(payload.get("session_id"))
        reasons = verdict.get("reasons", []) if verdict else []
        _append_verdict(Path(log_dir), {
            "ts": _ts(), "session_id": session_id,
            "tool_use_id": payload.get("tool_use_id"),
            "status": "review" if verdict else "pass",
            "reasons": reasons,
        })
        if not verdict:
            return None

        why = "; ".join(verdict["reasons"])
        return {
            "systemMessage": f"Jev review-gate: {why}. Suggests {verdict['who']}.",
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": review_context(verdict),
            },
        }
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        from jev.client import LOG_DIR
        from jev.review_gate import assess_output
        output = handle(payload, LOG_DIR, assess_output) if isinstance(payload, dict) else None
        if output:
            print(json.dumps(output, ensure_ascii=True))
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
