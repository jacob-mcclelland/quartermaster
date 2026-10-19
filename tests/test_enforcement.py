"""Offline test of the one blocking check: deny a routed edit, then allow it
after a logged veto. Runs in a temporary log folder, never the real logs/.
"""

import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSION = "enforcement-test-session"
PROMPT = "enforcement test prompt"


def load(name, folder):
    spec = importlib.util.spec_from_file_location(name, ROOT / folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def invoke(module, payload=None, args=None):
    old_in, old_out, old_err, old_argv = sys.stdin, sys.stdout, sys.stderr, sys.argv
    sys.stdin = io.StringIO(json.dumps(payload or {}))
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    sys.argv = [str(module.__file__), *(args or [])]
    try:
        code = module.main()
        return code, sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr, sys.argv = old_in, old_out, old_err, old_argv


def check(name, cond, detail=""):
    if not cond:
        print(f"FAIL: {name} {detail}")
        raise SystemExit(1)


def edit(path):
    return {"session_id": SESSION, "tool_input": {"file_path": path}}


def main() -> int:
    gate = load("plan_gate", "hooks")
    veto = load("route_veto", "scripts")
    old_session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp)
        gate.LOG_DIR = veto.LOG_DIR = log_dir
        turn = hashlib.sha256(PROMPT.encode()).hexdigest()[:16]
        (log_dir / f".last-prompt-{SESSION}.json").write_text(
            json.dumps({"prompt": PROMPT}), encoding="utf-8")
        (log_dir / f".route-plan-turn-{SESSION}.json").write_text(json.dumps({
            "turn": turn, "worker": "cheap_worker", "files": ["src/example.py"],
            "veto": False, "session_id": SESSION}), encoding="utf-8")

        check("routed file is denied",
              '"permissionDecision": "deny"' in invoke(gate, edit("C:/repo/src/example.py"))[1])
        check("unrouted file is allowed",
              "deny" not in invoke(gate, edit("C:/repo/src/unrelated.py"))[1])
        os.environ["CLAUDE_CODE_SESSION_ID"] = SESSION
        try:
            check("a short veto reason is refused", invoke(veto, args=["too short"])[0] == 2)
            check("a specific veto reason is accepted",
                  invoke(veto, args=["This task requires an explicit inline override."])[0] == 0)
        finally:
            if old_session is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = old_session
        vetoes = (log_dir / "vetoes.jsonl").read_text(encoding="utf-8").splitlines()
        check("exactly one veto logged", len(vetoes) == 1, vetoes)
        check("the veto records its reason",
              json.loads(vetoes[0])["reason"] == "This task requires an explicit inline override.")
        check("after the veto, the routed file is allowed",
              "deny" not in invoke(gate, edit("C:/repo/src/example.py"))[1])
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
