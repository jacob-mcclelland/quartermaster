"""Run every offline test_*.py file as its own process."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        # Point the Codex usage reader at an empty folder and a scratch cache,
        # so results do not depend on this machine's Codex logs.
        env = dict(os.environ,
                   AGENT_CHECKS_CODEX_SESSIONS=str(Path(tmp) / "sessions"),
                   AGENT_CHECKS_CODEX_USAGE_FILE=str(Path(tmp) / "codex-usage.json"))
        # Name-based discovery registers test_gate_async.py with the other tests.
        for path in sorted(root.glob("test_*.py")):
            result = subprocess.run([sys.executable, str(path)], cwd=root.parent,
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", env=env)
            print(f"{path.name}: {'PASS' if result.returncode == 0 else 'FAIL'}")
            if result.stdout: print(result.stdout.rstrip())
            if result.stderr: print(result.stderr.rstrip())
            if result.returncode: failures.append(path.name)
    return 1 if failures else 0

if __name__ == "__main__":
    raise SystemExit(main())
