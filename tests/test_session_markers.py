"""Offline regression tests for session-scoped routing marker files."""

import datetime as dt
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
SCRIPTS = ROOT / "scripts"
SESS_A = "sess-A"
SESS_B = "sess-B"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, (HOOKS if name in {"route_hook", "plan_gate"} else SCRIPTS) / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _invoke(module, payload=None, argv=None, env_updates=None):
    old_stdin, old_stdout, old_stderr, old_argv = sys.stdin, sys.stdout, sys.stderr, sys.argv
    old_env = os.environ.copy()
    out, err = io.StringIO(), io.StringIO()
    try:
        sys.stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload or {}))
        sys.stdout, sys.stderr = out, err
        sys.argv = argv or [str(module.__file__)]
        os.environ.clear()
        os.environ.update(old_env)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if env_updates:
            os.environ.update(env_updates)
        code = module.main()
        return code, out.getvalue(), err.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr, sys.argv = old_stdin, old_stdout, old_stderr, old_argv
        os.environ.clear()
        os.environ.update(old_env)


def _hook(hook, session_id: str, prompt: str):
    return _invoke(hook, {"session_id": session_id, "prompt": prompt})


def _gate(gate, session_id, path):
    payload = {"session_id": session_id, "tool_input": {"file_path": path}}
    return _invoke(gate, payload)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _real_log_files(log_dir: Path) -> set[str]:
    return ({str(path.relative_to(log_dir)) for path in log_dir.rglob("*") if path.is_file()}
            if log_dir.exists() else set())


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def run_checks() -> None:
    real_log_dir = ROOT / "logs"
    real_files_before = _real_log_files(real_log_dir)
    hook = _load("route_hook")
    route_plan = _load("route_plan")
    gate = _load("plan_gate")
    veto = _load("route_veto")

    with tempfile.TemporaryDirectory(prefix="session-markers-") as temp:
        temp_log_dir = Path(temp)
        for module in (hook, route_plan, gate, veto):
            module.LOG_DIR = temp_log_dir

        prompt_a = temp_log_dir / f".last-prompt-{SESS_A}.json"
        prompt_b = temp_log_dir / f".last-prompt-{SESS_B}.json"
        marker_a = temp_log_dir / f".route-plan-turn-{SESS_A}.json"
        marker_b = temp_log_dir / f".route-plan-turn-{SESS_B}.json"
        pointer = temp_log_dir / ".current-session.json"

        _hook(hook, SESS_A, "prompt A1")
        _hook(hook, SESS_B, "prompt B1")
        a_session = route_plan.resolve_session([], {"CLAUDE_CODE_SESSION_ID": SESS_A})
        route_plan.write_marker(a_session, {"files": ["src/x.py"]}, {"worker": "orchestrator"})
        _hook(hook, SESS_B, "prompt B2")

        code, output, _ = _gate(gate, SESS_A, "src/x.py")
        _check(code == 0 and output == "", "A's routed turn should be silent after B's prompt")
        code, output, _ = _gate(gate, SESS_B, "src/x.py")
        _check(code == 0 and "plan-gate" in output, "B should nag for its unrouted turn")
        print("1. Interleaving regression: PASS")

        code, output, _ = _gate(gate, SESS_B, "src/x.py")
        _check(code == 0 and output == "", "B should nag only once for the turn")
        print("2. B nags once: PASS")

        selected = route_plan.resolve_session(
            ["--session", SESS_B], {"CLAUDE_CODE_SESSION_ID": SESS_A})
        _check(selected == SESS_B, "--session must override the environment session")
        route_plan.write_marker(selected, {"files": ["src/x.py"]}, {"worker": "cheap_worker"})
        code, output, _ = _gate(gate, SESS_B, "C:/r/src/x.py")
        _check(code == 0 and '"permissionDecision": "deny"' in output,
               "B's worker marker should deny an edit to its routed file")
        code, output, _ = _gate(gate, SESS_A, "C:/r/src/x.py")
        _check(code == 0 and "deny" not in output,
               "B's marker must not deny A's edit")
        print("3. Deny isolation: PASS")

        marker_a_before = marker_a.read_bytes()
        reason = "The change is needed inline for this session's task."
        code, _, error = _invoke(
            veto, argv=[str(veto.__file__), reason],
            env_updates={"CLAUDE_CODE_SESSION_ID": SESS_B})
        _check(code == 0, f"route_veto should succeed for B: {error.strip()}")
        _check(marker_a.read_bytes() == marker_a_before,
               "B's veto must leave A's marker bytes unchanged")
        _check(_read_json(marker_b).get("veto") is True,
               "B's marker should record the veto")
        code, output, _ = _gate(gate, SESS_B, "src/x.py")
        _check(code == 0 and "deny" not in output,
               "B's veto should disable the routed-file deny")
        print("4. Veto isolation: PASS")

        prompt_a_before = prompt_a.read_bytes()
        pointer_before = pointer.read_bytes()
        _hook(hook, SESS_A, "<task-notification>background task done</task-notification>")
        _check(prompt_a.read_bytes() == prompt_a_before,
               "task notification must not overwrite A's saved prompt")
        _check(pointer.read_bytes() == pointer_before,
               "task notification must not update the current-session pointer")
        print("5. Task notification preserves state: PASS")

        _hook(hook, SESS_A, "prompt A2")
        fallback = route_plan.resolve_session([], {})
        _check(fallback == SESS_A, "pointer fallback should choose the latest hook session")
        print("6. Pointer fallback: PASS")

        code, output, _ = _invoke(gate, {"tool_input": {"file_path": "src/x.py"}})
        _check(code == 0 and output == "", "missing session id must fail open silently")
        marker_a.write_text("{broken", encoding="utf-8")
        code, _, _ = _gate(gate, SESS_A, "src/x.py")
        _check(code == 0, "corrupt marker should fail open without raising")
        print("7. Fail-open behavior: PASS")

        # Only this test's sessions count: concurrent real sessions legitimately
        # create their own session-keyed files in logs/ while this runs.
        leaked = {name for name in _real_log_files(real_log_dir) - real_files_before
                  if SESS_A in name or SESS_B in name}
        _check(not leaked,
               f"the test must not create files in the real logs directory: {sorted(leaked)}")
        print("8. No real-log writes: PASS")


def main() -> int:
    try:
        run_checks()
    except Exception as error:  # noqa: BLE001
        print(f"FAIL: {error}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
