"""Offline tests for which edits the plan gate treats as routed work.

The gate is the only check that blocks, so it must match the files the route
named and nothing else.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("plan_gate", ROOT / "hooks" / "plan_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
match = gate._matches_routed_file


def check(name, cond, detail=""):
    if not cond:
        print(f"FAIL: {name} {detail}")
        raise SystemExit(1)


def main() -> int:
    for edited in ("C:/repo/src/example.py", "/home/u/repo/src/example.py",
                   "C:\\repo\\src\\example.py", "C:/repo/./src/example.py"):
        check(f"routed src/example.py matches {edited}", match(edited, ["src/example.py"]))
    check("leading ./ in the route still matches",
          match("C:/repo/src/example.py", ["./src/example.py"]))
    for edited in ("C:/repo/other/example.py", "C:/repo/mysrc/example.py"):
        check(f"no match across a folder boundary: {edited}",
              not match(edited, ["src/example.py"]))

    check("absolute route matches itself", match("C:/repo/src/example.py", ["C:/repo/src/example.py"]))
    check("absolute route, other drive", not match("D:/repo/src/example.py", ["C:/repo/src/example.py"]))
    check("absolute route, other folder", not match("C:/other/src/example.py", ["C:/repo/src/example.py"]))

    check("bare name in the working folder matches",
          match("C:/repo/README.md", ["README.md"], cwd="C:/repo"))
    check("bare name elsewhere does not match",
          not match("C:/other/project/README.md", ["README.md"], cwd="C:/repo"))
    check("bare name without a working folder matches nothing",
          not match("C:/repo/README.md", ["README.md"]))

    check("non-list routes match nothing", not match("C:/repo/a.py", "a.py"))
    check("blank entries are ignored", not match("C:/repo/a.py", ["", "  ", None]))
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
