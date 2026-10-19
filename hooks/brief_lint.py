"""PreToolUse hook: lint a delegated task brief before it reaches a worker.

Fires on Codex dispatches. Reads the brief, asks Jev whether it is specified
well enough for a cheap worker to finish, and reports back. Advisory only:
it never blocks.

Why specification and not difficulty: difficulty is not in the brief, so
asking "is this hard?" gets a confident guess (observed in this project's own
routing tests). "Are the files named? Is there a test command?" is fully
present in the text. A failing answer is also actionable: it says how to fix
the brief, whichever model then runs it.

Fails open, always. A linter that blocks work when an API is down is worse
than no linter.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QUESTIONS = {
    "targets_named": {
        "instructions": "The task identifies precisely what it is about: "
        "either the specific files, paths or functions to change, or, for a "
        "task about finding things out, the specific subject: which document, "
        "which source, which person, which exact question to answer.",
    },
    "done_is_checkable": {
        "instructions": "The task states a command, test or check whose "
        "result decides whether the work is complete.",
    },
    "scope_bounded": {
        "instructions": "The task states what may be changed and what must "
        "be left alone.",
    },
    "needs_repo_wide_context": {
        "instructions": "Completing this task requires understanding code "
        "spread across many files, beyond the ones named in the task.",
    },
    "needs_judgement": {
        "instructions": "This task requires deciding between approaches or "
        "exercising design judgement, rather than carrying out a decision "
        "that has already been made.",
    },
    # Research briefs have no exit code to check; their output is a report,
    # verified by reading it. Demanding a test command from them produced a
    # false positive on the first good brief we tried, and a linter that
    # cries wolf gets ignored.
    "is_research": {
        "instructions": "The task asks the worker to find things out and "
        "report back (reading, searching or analysing) rather than to "
        "change any file.",
    },
    "output_defined": {
        "instructions": "The task states what the answer should look like: "
        "its format, structure, or what it must contain.",
    },
}


def extract_brief(payload: dict) -> str | None:
    """Pull the brief out of whichever kind of delegation this is.

    Two paths: a Codex dispatch (a Bash command) and a Claude subagent (the
    Agent/Task tool). The second was uncovered until 2026-09-19 and is the
    larger half: one session spawned eleven subagents and zero Codex workers
    (observed in this project's own use).
    """
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}

    if tool in ("Agent", "Task"):
        return ti.get("prompt")

    command = ti.get("command", "")
    if DISPATCH.search(command):
        m = BRIEF_ARG.search(command)
        path = next((g for g in m.groups() if g), None) if m else None
        return _read(path)
    if RAW_EXEC.search(command):
        # The brief is either read from a file or passed inline as the last
        # quoted argument.
        m = re.search(r"""\$\(cat\s+["']?([^"')]+)["']?\)""", command)
        if m:
            return _read(m.group(1))
        quoted = re.findall(r'"((?:[^"\\]|\\.){40,})"', command, re.DOTALL)
        return quoted[-1] if quoted else None
    return None


# Dispatch moved from the companion script to `codex exec` on 2026-09-28,
# and this hook went blind to every dispatch until 2026-10-01.
DISPATCH = re.compile(r"dispatch\.py\b")
BRIEF_ARG = re.compile(r"""--brief\s+(?:"([^"]+)"|'([^']+)'|(\S+))""")
RAW_EXEC = re.compile(r"(?:^|[\s;&|(])codex(?:\.cmd)?\s+exec\s")


def _read(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0

    brief = extract_brief(payload)
    command = (payload.get("tool_input") or {}).get("command", "")
    raw = bool(RAW_EXEC.search(command)) and not DISPATCH.search(command)
    nudge = ("Raw `codex exec` skips the review gate and the source check, "
             "and hangs if stdin is left open. Dispatch through "
             f"`python {Path(__file__).resolve().parent.parent / 'scripts' / 'dispatch.py'}` instead.") if raw else ""
    if not brief or len(brief) < 40:
        if nudge:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "additionalContext": nudge}}))
        return 0

    try:
        from jev import Jev, noul

        client = Jev()
        answers = client.ask(
            {"task_brief": brief},
            {k: noul(**v) for k, v in QUESTIONS.items()},
            debias=False,  # all Noul: nothing to reverse
            label="brief_lint",
        )
    except Exception:  # noqa: BLE001 - key missing, API down, anything
        if nudge:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "additionalContext": nudge}}))
        return 0

    def p(key: str) -> float:
        return answers.get(key, {}).get("value") or 0.0

    from jev.thresholds import load as load_thresholds
    t = load_thresholds()
    research = p("is_research") > t["brief_research_at"]

    weak = []
    if p("targets_named") < t["brief_spec_at"]:
        weak.append("no specific files, sources or functions named")
    if research:
        # Judge research on whether the answer's shape is defined, not on a
        # test command it could never have.
        if p("output_defined") < t["brief_spec_at"]:
            weak.append("no stated shape for the answer")
    elif p("done_is_checkable") < t["brief_spec_at"]:
        weak.append("no command or test that decides when it's done")
    if p("scope_bounded") < t["brief_spec_at"]:
        weak.append("scope not bounded")

    escalate = []
    if p("needs_repo_wide_context") > t["brief_escalate_at"]:
        escalate.append("needs repo-wide context beyond what the brief names")
    if p("needs_judgement") > t["brief_escalate_at"]:
        escalate.append("needs design judgement, not just execution")

    if not weak and not escalate:
        if nudge:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "additionalContext": nudge}}))
        return 0

    parts = []
    if weak:
        parts.append("Brief is thin: " + "; ".join(weak) + ".")
    if escalate:
        parts.append("May not suit a cheap worker: " + "; ".join(escalate) + ".")

    msg = " ".join(parts)
    print(json.dumps({
        "systemMessage": f"Jev brief-lint: {msg}",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                f"Brief lint before dispatch. {msg} "
                "Consider tightening the brief (name the files, give a command "
                "whose exit code decides done, state what not to touch) or "
                "handling it yourself. Advisory only; these are calibrated "
                "against nothing yet, so use judgement." + (" " + nudge if nudge else "")
            ),
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
