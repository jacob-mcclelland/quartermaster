"""Pin the exact wording of every question sent to Jev.

Wording moves answers, so a threshold chosen against one wording does not
carry over to another (the call log hashes each question for the same
reason). Any edit to a question, even punctuation, fails this test. If the
change is deliberate, update the hash here and revisit the thresholds in
jev/thresholds.py that read that question.
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jev import judge, plan_router, review_gate  # noqa: E402

spec = importlib.util.spec_from_file_location("brief_lint", ROOT / "hooks" / "brief_lint.py")
brief_lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief_lint)

PINNED = {
    "router": "eb6f26d5692ffc78",
    "review gate": "d9d3b765e6c90afc",
    "brief lint": "02be860400f825c0",
    "source check": "a23cb7d1be0049eb",
}


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def main() -> int:
    current = {
        "router": digest(plan_router.QUESTIONS),
        "review gate": digest({**review_gate.QUESTIONS, **review_gate.DIFF_QUESTIONS}),
        "brief lint": digest(brief_lint.QUESTIONS),
        "source check": digest({"screen": judge.SCREEN_QUESTIONS,
                                "relations": judge.RELATIONS,
                                "anti_injection": judge.ANTI_INJECTION}),
    }
    changed = [name for name in PINNED if current[name] != PINNED[name]]
    if changed:
        for name in changed:
            print(f"FAIL: {name} question wording changed "
                  f"({PINNED[name]} -> {current[name]}); thresholds chosen "
                  "against the old wording no longer apply")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
