"""Offline tests for the digest router: what it rejects, and where it sends work.

A fake client stands in for Jev, so each policy branch can be driven directly.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jev import plan_router  # noqa: E402
from jev.thresholds import DEFAULTS as T  # noqa: E402

BASE = {
    "goal": "Change the parser",
    "files": ["src/parser.py"],
    "change": "Parse empty input as an empty list",
    "done_check": "python -m unittest tests.test_parser",
    "depends_on_conversation": "nothing",
    "out_of_scope": "Other files",
    "audience": "Maintainers",
    "reversibility": "git revert",
}


class Fake:
    """Answers every router question with fixed values."""

    def __init__(self, work_type="mechanical", design_p=0.0, **values):
        self.work_type, self.design_p = work_type, design_p
        self.values = {"complexity": 1.0, "needs_long_context": 0.0,
                       "brief_is_sufficient": 0.9, "dependencies_are_portable": 0.9,
                       "check_proves_done": 0.9, "stakes": 0.0, "splits_cleanly": 0.0,
                       **values}

    def ask(self, state, questions, label=None, **kw):
        answers = {k: {"value": v} for k, v in self.values.items() if k in questions}
        answers["work_type"] = {"value": self.work_type,
                                "probabilities": {"design": self.design_p, "investigation": 0.0}}
        return answers


def check(name, cond, detail=""):
    if not cond:
        print(f"FAIL: {name} {detail}")
        raise SystemExit(1)


def route(digest=None, **kw):
    return plan_router.route(digest or BASE, Fake(**kw))


def test_validate():
    for word in ("simple", "easy", "trivial", "just", "risky", "safe", "small", "huge"):
        problems = plan_router.validate({**BASE, "goal": f"Make the {word} parser change"})
        check(f"judgement word rejected: {word}", any("judgement words" in p for p in problems), problems)
    problems = plan_router.validate({**BASE, "expected_worker": "cheap_worker"})
    check("destination field rejected", any("expected destination" in p for p in problems), problems)
    for field in plan_router.REQUIRED:
        problems = plan_router.validate({k: v for k, v in BASE.items() if k != field})
        check(f"missing {field} reported", f"missing field: {field}" in problems, problems)
    check("clean digest passes", plan_router.validate(BASE) == [], plan_router.validate(BASE))


def test_branches():
    check("design stays with the orchestrator", route(work_type="design")["worker"] == "orchestrator")
    check("high-stakes design gets a consult",
          route(work_type="design", stakes=T["high_stakes_at"])["worker"] == "consult_then_orchestrator")
    check("design mass below the top choice still keeps the work",
          route(design_p=T["design_mass_block"])["worker"] == "orchestrator")
    check("unportable brief stays",
          route(brief_is_sufficient=T["delegate_at"] - 0.01)["worker"] == "orchestrator")
    r = route(needs_long_context=T["long_context_at"] + 0.01)
    check("long context goes to the long-context worker", r["worker"] == "long_context_worker", r)
    check("long-context effort comes from config", r["effort"] == "medium", r)
    check("high complexity stays",
          route(complexity=T["keep_complexity_at"])["worker"] == "orchestrator")
    r = route()
    check("default goes to the cheap worker", r["worker"] == "cheap_worker", r)
    check("cheap-worker effort comes from config", r["effort"] == "xhigh", r)
    check("strong done-check is trusted", r["after"].startswith("run the done-check yourself"), r)
    r = route(check_proves_done=T["done_check_at"] - 0.01)
    check("weak done-check plans a review", r["after"].startswith("done-check is weak"), r)
    r = route(stakes=T["high_stakes_at"])
    check("high stakes adds a cross-family review",
          "a reviewer from a different model family" in r["after"], r)


def test_research_and_network():
    r = route(work_type="research", complexity=T["keep_complexity_at"])
    check("portable research goes to the cheap worker", r["worker"] == "cheap_worker", r)
    original = plan_router._load_config
    try:
        plan_router._load_config = lambda: {**original(), "workers_have_network": False}
        check("research stays without worker network",
              route(work_type="research", complexity=T["keep_complexity_at"])["worker"] == "orchestrator")
        check("research never goes to a worker without network",
              route(work_type="research")["worker"] == "orchestrator")
    finally:
        plan_router._load_config = original


def test_protected_paths():
    home_file = str(Path.home() / ".claude" / "rules.txt")
    for entry in (home_file, "~/.claude/rules.txt", "~/.codex/../.claude/x.json"):
        check(f"protected: {entry}", plan_router.unreachable_files([entry]) == [entry])
        check(f"protected path keeps work in session: {entry}",
              route({**BASE, "files": [entry]})["worker"] == "orchestrator")
    for entry in (".claude/rules.txt", "src/parser.py", "https://example.com/.claude"):
        check(f"not protected: {entry}", plan_router.unreachable_files([entry]) == [])


def test_fanout():
    one, two = {**BASE, "pieces": ["a"]}, {**BASE, "pieces": ["a", "b"]}
    check("one piece never fans out", not route(one, splits_cleanly=0.99)["fanout"])
    check("below the cut, no fan-out", not route(two, splits_cleanly=T["fanout_at"] - 0.01)["fanout"])
    check("at the cut, fan out", route(two, splits_cleanly=T["fanout_at"])["fanout"])


def main() -> int:
    test_validate()
    test_branches()
    test_research_and_network()
    test_protected_paths()
    test_fanout()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
