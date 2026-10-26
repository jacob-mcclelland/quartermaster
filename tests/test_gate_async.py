"""Offline checks for async review-gate handling. No API key or network."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _no_network(*args, **kwargs):
    raise AssertionError("network call attempted")


urllib.request.urlopen = _no_network

from hooks import review_gate_hook, route_hook  # noqa: E402
from scripts import dispatch  # noqa: E402
from jev.review_gate import assess_output  # noqa: E402


REVIEW = {
    "who": "a fresh-context reviewer",
    "why": "contained change, a clean second read is enough",
    "reasons": ["nothing actually ran to confirm it works"],
}


class FakeAssess:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = []

    def __call__(self, summary):
        self.calls.append(summary)
        return self.verdict


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _launch(log_dir, session_id, agent_id, prompt="Review the requested work carefully."):
    payload = {
        "session_id": session_id,
        "tool_use_id": f"toolu-{agent_id}",
        "tool_name": "Agent",
        "tool_input": {"prompt": prompt},
        "tool_response": {
            "isAsync": True,
            "status": "async_launched",
            "agentId": agent_id,
            "description": "test agent",
        },
    }
    return review_gate_hook.handle(payload, log_dir, FakeAssess(None))


def _notification(agent_id, result=None, status="completed"):
    return ("wrapper text before\n<task-notification>\n"
            f"<task-id>{agent_id}</task-id>\n"
            "<tool-use-id>toolu_done</tool-use-id>\n"
            f"<status>{status}</status>\n"
            + (f"<result>{result}</result>\n" if result is not None else "")
            + "</task-notification>\nwrapper text after")


def check_async_launches(log_dir):
    assess = FakeAssess(REVIEW)
    payload = {
        "session_id": "launch-session",
        "tool_use_id": "toolu-launch",
        "tool_name": "Agent",
        "tool_input": {"prompt": "Please complete this task and report your findings."},
        "tool_response": {
            "isAsync": True,
            "status": "async_launched",
            "agentId": "agent-123",
            "description": "background review",
        },
    }
    output = review_gate_hook.handle(payload, log_dir, assess)
    _check(output is None and not assess.calls,
           "dict async launch must stay silent and skip assessment")
    pending_path = log_dir / ".gate-pending-launch-session.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    saved = pending["agent-123"]
    _check(saved["asked"] == payload["tool_input"]["prompt"], "launch task should be saved")
    _check(saved["description"] == "background review", "launch description should be saved")
    _check(saved["tool_use_id"] == "toolu-launch" and saved["ts"],
           "launch metadata should be saved")
    rows = _lines(log_dir / "review-gate-verdicts.jsonl")
    _check(rows[-1]["status"] == "pending" and rows[-1]["agent_id"] == "agent-123",
           "dict launch should append a pending verdict row")

    string_payload = {
        "session_id": "string-session",
        "tool_use_id": "toolu-string",
        "tool_name": "Task",
        "tool_input": {"prompt": "Please finish the string-form background task."},
        "tool_response": "Async agent launched successfully. agentId: abc outputFile: result.txt",
    }
    string_assess = FakeAssess(REVIEW)
    _check(review_gate_hook.handle(string_payload, log_dir, string_assess) is None,
           "string async launch should stay silent")
    _check(not string_assess.calls, "string async launch must skip assessment")
    string_pending = json.loads(
        (log_dir / ".gate-pending-string-session.json").read_text(encoding="utf-8"))
    _check("abc" in string_pending, "string async launch should save its agent ID")


def check_notifications(log_dir):
    _launch(log_dir, "notify-session", "agent-running",
            "Review the running agent task and report its outcome.")
    _launch(log_dir, "notify-session", "agent-no-result",
            "Review the agent task that omitted its result.")
    _launch(log_dir, "notify-session", "agent-done",
            "Update the requested module, then report exactly what changed and what ran.")
    eligibility_assess = FakeAssess(REVIEW)
    for expected_calls, (agent_id, status) in enumerate((
            ("agent-running", "running"),
            ("agent-no-result", "completed")), start=1):
        notification = _notification(agent_id, status=status)
        context = route_hook.handle_notifications(
            notification, "notify-session", log_dir, eligibility_assess,
        )
        _check(context and "[review-gate]" in context,
               "matching notification should be assessed regardless of status or result")
        _check(len(eligibility_assess.calls) == expected_calls,
               "each matching edge notification should be assessed once")
        _check(eligibility_assess.calls[-1].endswith("WHAT THE WORKER REPORTED:\n"),
               "notification without a result should be assessed with an empty result")
    pending_path = log_dir / ".gate-pending-notify-session.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    _check("agent-running" not in pending and "agent-no-result" not in pending,
           "assessed notifications should remove their pending records")

    assess = FakeAssess(REVIEW)
    prompt = _notification("agent-done", "Updated the module and ran the focused checks.")
    context = route_hook.handle_notifications(prompt, "notify-session", log_dir, assess)
    _check(len(assess.calls) == 1, "matching notification should be assessed once")
    _check("Update the requested module" in assess.calls[0],
           "assessment summary should include the saved task")
    _check("ran the focused checks" in assess.calls[0],
           "assessment summary should include the result")
    _check("TASK GIVEN TO THE WORKER:" in assess.calls[0]
           and "WHAT THE WORKER REPORTED:" in assess.calls[0],
           "assessment should use the review-gate summary structure")
    _check(context and "[review-gate]" in context,
           "review verdict should return review context")
    rows = _lines(log_dir / "review-gate-verdicts.jsonl")
    review = next(row for row in reversed(rows) if row.get("agent_id") == "agent-done")
    _check(review["status"] == "review" and review["reasons"] == REVIEW["reasons"],
           "completion review verdict should be logged")
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    _check("agent-done" not in pending, "completion should remove the pending entry")

    _check(route_hook.handle_notifications(prompt, "notify-session", log_dir, assess) is None,
           "duplicate completion should do nothing")
    _check(len(assess.calls) == 1, "duplicate completion must not assess again")

    unmatched = _notification("shell-job-44", "A background shell command completed.")
    _check(route_hook.handle_notifications(unmatched, "notify-session", log_dir, assess) is None,
           "unmatched shell completion should do nothing")
    _check(len(assess.calls) == 1, "unmatched completion must not be assessed")

    _launch(log_dir, "multi-session", "agent-match",
            "Handle the matching background agent task and report the outcome.")
    two_blocks = (_notification("shell-job-9", "Shell output.") + "\n"
                  + _notification("agent-match", "The requested agent task completed."))
    multi_assess = FakeAssess(None)
    _check(route_hook.handle_notifications(two_blocks, "multi-session", log_dir,
                                           multi_assess) is None,
           "pass verdict should not return review context")
    _check(len(multi_assess.calls) == 1 and "requested agent task completed" in multi_assess.calls[0],
           "only the matching block should be assessed")
    rows = _lines(log_dir / "review-gate-verdicts.jsonl")
    passed = next(row for row in reversed(rows) if row.get("agent_id") == "agent-match")
    _check(passed["status"] == "pass" and passed["reasons"] == [],
           "completion with no verdict should be logged as pass")


def check_sync_agent(log_dir):
    assess = FakeAssess(REVIEW)
    payload = {
        "session_id": "sync-session",
        "tool_use_id": "toolu-sync",
        "tool_name": "Agent",
        "tool_input": {"prompt": "Please complete the synchronous task and report the exact outcome."},
        "tool_response": "Finished the work, but no check was run.",
    }
    output = review_gate_hook.handle(payload, log_dir, assess)
    _check(len(assess.calls) == 1, "synchronous Agent result should still be assessed")
    _check(output and "[review-gate]" in output["hookSpecificOutput"]["additionalContext"],
           "synchronous review should return the shared review context")


def check_read_only_assessment():
    class FakeClient:
        def __init__(self):
            self.state = None
            self.questions = None

        def ask(self, state, questions, label):
            self.state = state
            self.questions = questions
            return {key: {"value": value} for key, value in {
                "was_verified": 0.1,
                "touches_many_files": 0.1,
                "user_facing": 0.9,
                "hard_to_reverse": 0.1,
                "scope_drift": 0.1,
                "risk": 1.6,
            }.items() if key in questions}

    client = FakeClient()
    assess_output("Read-only inspection report", client=client,
                  diff="diff text that should not be evaluated", read_only=True)
    _check("diff_matches_task" not in client.questions,
           "read-only questions must omit diff_matches_task even with a diff")
    _check("This was a read-only review: no file changes were expected, so an empty change list is correct."
           in client.state["note"],
           "read-only state note should explain the empty change list")

    dispatch_client = FakeClient()
    dispatch.run_gate("read-only task", "result", [], "diff text",
                      client=dispatch_client, read_only=True)
    _check("diff_matches_task" not in dispatch_client.questions,
           "read-only dispatch should tell the gate to skip the diff question")


def _prepare_isolated_hooks(root, gate_source=None):
    root = Path(root)
    hooks_dir = root / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "__init__.py").write_text("", encoding="utf-8")
    route_path = hooks_dir / "route_hook.py"
    shutil.copyfile(ROOT / "hooks" / "route_hook.py", route_path)
    gate_path = hooks_dir / "review_gate_hook.py"
    if gate_source is None:
        shutil.copyfile(ROOT / "hooks" / "review_gate_hook.py", gate_path)
    else:
        gate_path.write_text(gate_source, encoding="utf-8")

    jev_dir = root / "jev"
    jev_dir.mkdir()
    (jev_dir / "__init__.py").write_text("", encoding="utf-8")
    (jev_dir / "client.py").write_text(
        "from pathlib import Path\n"
        "LOG_DIR = Path(__file__).resolve().parents[1] / 'logs'\n",
        encoding="utf-8",
    )
    em_dash = chr(0x2014)
    (jev_dir / "review_gate.py").write_text(
        "def assess_output(summary):\n"
        f"    return {{'who': 'fresh reviewer', 'why': 'check carefully {em_dash}', "
        f"'reasons': ['confirm output {em_dash}']}}\n",
        encoding="utf-8",
    )
    return route_path, gate_path


def _run_hook(script_path, root, payload):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "cp1252"
    return subprocess.run(
        [sys.executable, str(script_path)],
        input=json.dumps(payload).encode("ascii"),
        capture_output=True,
        cwd=root,
        env=env,
        check=False,
    )


def check_ascii_json_output():
    em_dash = chr(0x2014)
    with tempfile.TemporaryDirectory(prefix="gate-cp1252-") as temp:
        root = Path(temp)
        route_path, gate_path = _prepare_isolated_hooks(root)
        log_dir = root / "logs"
        log_dir.mkdir()
        pending_path = log_dir / ".gate-pending-test-session.json"
        pending_path.write_text(json.dumps({
            "agent-output": {"asked": "Please review the requested change.",
                             "tool_use_id": "toolu-output"},
        }), encoding="utf-8")

        route_run = _run_hook(route_path, root, {
            "prompt": _notification("agent-output", "Completed the requested work."),
            "session_id": "test-session",
        })
        _check(route_run.returncode == 0,
               "route hook should exit successfully with CP1252 stdout")
        _check(all(byte < 128 for byte in route_run.stdout),
               "route hook JSON stdout should contain ASCII bytes only")
        route_output = json.loads(route_run.stdout.decode("cp1252"))
        route_context = route_output["hookSpecificOutput"]["additionalContext"]
        _check(em_dash in route_context,
               "route hook JSON should parse back to the original Unicode text")

        gate_run = _run_hook(gate_path, root, {
            "session_id": "cp1252-session",
            "tool_name": "Agent",
            "tool_input": {"prompt": "Please complete this delegated task and report the exact outcome."},
            "tool_response": "Finished the requested work.",
        })
        _check(gate_run.returncode == 0,
               "review gate should exit successfully with CP1252 stdout")
        _check(all(byte < 128 for byte in gate_run.stdout),
               "review gate JSON stdout should contain ASCII bytes only")
        gate_output = json.loads(gate_run.stdout.decode("cp1252"))
        _check(em_dash in gate_output["systemMessage"],
               "review gate JSON should parse back to the original Unicode text")
        _check(em_dash in gate_output["hookSpecificOutput"]["additionalContext"],
               "review context JSON should parse back to the original Unicode text")


def check_gate_import_error_fails_open():
    with tempfile.TemporaryDirectory(prefix="gate-import-error-") as temp:
        root = Path(temp)
        route_path, _ = _prepare_isolated_hooks(
            root, gate_source="raise ImportError('simulated gate import failure')\n",
        )
        log_dir = root / "logs"
        log_dir.mkdir()
        pending_path = log_dir / ".gate-pending-import-session.json"
        pending = {"agent-import": {"asked": "Finish the work.",
                                    "tool_use_id": "toolu-import"}}
        pending_path.write_text(json.dumps(pending), encoding="utf-8")

        run = _run_hook(route_path, root, {
            "session_id": "import-session",
            "prompt": _notification("agent-import", "Completed the delegated task."),
        })
        _check(run.returncode == 0,
               "route hook should exit successfully if gate import fails")
        output = json.loads(run.stdout.decode("cp1252"))
        context = output["hookSpecificOutput"]["additionalContext"]
        _check("[router]" in context,
               "router reminder should survive a gate import failure")
        _check("[review-gate]" not in context,
               "notification review should be skipped if the gate import fails")
        _check(json.loads(pending_path.read_text(encoding="utf-8")) == pending,
               "notification handling should leave pending entries untouched on gate import failure")
        _check(not (log_dir / "review-gate-verdicts.jsonl").exists(),
               "notification handling should not write verdicts on gate import failure")


def check_non_string_prompt_fails_open():
    with tempfile.TemporaryDirectory(prefix="gate-non-string-prompt-") as temp:
        root = Path(temp)
        route_path, _ = _prepare_isolated_hooks(root)
        run = _run_hook(route_path, root, {"prompt": 42})
        _check(run.returncode == 0,
               "route hook should exit successfully for a truthy non-string prompt")


def main():
    try:
        with tempfile.TemporaryDirectory(prefix="gate-async-") as temp:
            log_dir = Path(temp)
            check_async_launches(log_dir)
            check_notifications(log_dir)
            check_sync_agent(log_dir)
        check_read_only_assessment()
        check_ascii_json_output()
        check_gate_import_error_fails_open()
        check_non_string_prompt_fails_open()
    except Exception as error:  # noqa: BLE001
        print(f"FAIL: {error}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
