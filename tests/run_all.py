"""Run every offline test_*.py file as its own process."""
import subprocess
import sys
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    failures = []
    # Name-based discovery registers test_gate_async.py with the other tests.
    for path in sorted(root.glob("test_*.py")):
        result = subprocess.run([sys.executable, str(path)], cwd=root.parent,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
        print(f"{path.name}: {'PASS' if result.returncode == 0 else 'FAIL'}")
        if result.stdout: print(result.stdout.rstrip())
        if result.stderr: print(result.stderr.rstrip())
        if result.returncode: failures.append(path.name)
    return 1 if failures else 0

if __name__ == "__main__":
    raise SystemExit(main())
