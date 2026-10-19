"""PostToolUse hook: decide whether finished delegated work needs reviewing.

Fires when an Agent or Task subagent returns. Builds a short record of what
was asked and what came back, and asks whether it has the shape that needs a
second pair of eyes. Codex dispatches are gated inside scripts/dispatch.py,
because a hook cannot see a background job's result.

Silent by default. Fails open.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MIN_CHARS = 60


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
        came_back = json.dumps(resp)[:4000]
    else:
        came_back = str(resp or "")[:4000]

    return (f"TASK GIVEN TO THE WORKER:\n{asked[:3000]}\n\n"
            f"WHAT THE WORKER REPORTED:\n{came_back}")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0

    summary = describe(payload)
    if not summary:
        return 0

    try:
        from jev.review_gate import assess_output

        verdict = assess_output(summary)
    except Exception:  # noqa: BLE001
        return 0

    if not verdict:
        return 0

    why = "; ".join(verdict["reasons"])
    print(json.dumps({
        "systemMessage": f"Jev review-gate: {why}. Suggests {verdict['who']}.",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"[review-gate] This finished work wants checking: {why}. "
                f"Send it to {verdict['who']} ({verdict['why']}). "
                "Give the reviewer the diff plus the surrounding code, the "
                "spec, and the real test output; withhold the author's "
                "reasoning and which model wrote it. Then turn every finding "
                "into a test before acting on it, because agreement between models "
                "is not proof. If a test could settle this instead, run the "
                "test and skip the review."
            ),
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
