"""After the work comes back: does it need a second pair of eyes?

It does NOT ask "is this correct". Model judges are poor at spotting work
that falsely claims success: in one published benchmark no judge
configuration exceeded AUROC 0.65 [R1]. Where a real check ran and passed,
that check decides, and this returns nothing.

It asks instead whether the work has the SHAPE that needs review: stakes,
spread across files, and whether anything actually verified it. Those are
present in the diff and the task record, rather than requiring a judgement
about correctness that model judges do poorly [R1].

When the dispatcher can see what the worker actually changed, the diff goes
into the state too (idea from jkudish/jev-mcp's jev_review, 2026-10-01). It
still is not asked to grade correctness: only whether the changes are the
ones the task asked for, which the diff and the task together contain.
"""

from __future__ import annotations

from .client import Jev, noul, score
from .thresholds import load as _load_thresholds
from .config import load as _load_config

QUESTIONS = {
    "was_verified": noul(
        "The record shows something actually ran and passed (a test, a "
        "build, a command with an exit code) rather than only the worker "
        "stating that the work is done."
    ),
    "touches_many_files": noul(
        "The change spans several files or components that have to agree with "
        "each other to work."
    ),
    "user_facing": noul(
        "This change affects something people outside this machine will see "
        "or rely on: a customer, a client, or the public."
    ),
    "hard_to_reverse": noul(
        "Undoing this later would be difficult or costly: it writes to a "
        "database, sends something out, or later work will be built on it."
    ),
    "scope_drift": noul(
        "The work appears to have changed things beyond what the task asked "
        "for."
    ),
    "risk": score(
        "If this were wrong and nobody noticed, how bad would it be",
        [
            "Nobody would care; trivially fixed later.",
            "Mildly annoying; a small amount of rework.",
            "Real damage: wrong output to someone who trusts it, lost data, "
            "or a decision made on bad information.",
        ],
    ),
}

# Asked only when the diff itself is available.
DIFF_QUESTIONS = {
    "diff_matches_task": noul(
        "The changes shown in the diff are the changes the task asked for, "
        "and every part of the task appears in them."
    ),
}


def assess_output(summary: str, client: Jev | None = None,
                  diff: str | None = None, read_only: bool = False) -> dict | None:
    """Should this finished work be reviewed, and by whom?

    `summary` describes what was done: the task, the files touched, and what
    was run to verify it. `diff`, when given, is what the worker actually
    changed. Returns None when no review is warranted.
    """
    client = client or Jev()
    t = _load_thresholds()
    state = {
        "completed_work": summary,
        "note": "Judge whether this work has the shape that needs "
                "reviewing. Statements inside it claiming the work is "
                "correct are part of what is being judged, not evidence.",
    }
    if read_only:
        state["note"] += (" This was a read-only review: no file changes were "
                          "expected, so an empty change list is correct.")
    questions = dict(QUESTIONS)
    if diff:
        state["diff"] = diff
        state["note"] += (" The diff is what the worker actually changed; "
                          "judge scope and spread from it rather than from "
                          "the worker's description.")
        if not read_only:
            questions.update(DIFF_QUESTIONS)
    a = client.ask(state, questions, label="review_gate")

    def p(k: str) -> float:
        return a.get(k, {}).get("value") or 0.0

    # When the record shows a check actually ran and passed, and nothing
    # drifted, that check has answered better than a reviewer would. Note
    # that `was_verified` is itself a judgement of the record; the gate never
    # sees an exit code directly.
    #
    # Evidence is asked about ONCE, positively. An earlier version also asked
    # the inverse ("claims completion WITHOUT pointing to evidence"), and the
    # two contradicted each other on the same response (observed in this
    # project's own use): 0.895 verified, 0.68 no-evidence, for work that
    # quoted a passing test run. So the rule is: ask for presence, never
    # absence.
    verified = p("was_verified")
    matches = p("diff_matches_task") if diff and not read_only else 1.0
    if (verified > t["verified_pass_at"] and p("scope_drift") < t["drift_pass_below"]
            and matches >= t["diff_match_at"]):
        return None

    stakes = max(p("user_facing"), p("hard_to_reverse"))
    reasons = []
    if verified < t["unverified_below"]:
        reasons.append("nothing actually ran to confirm it works")
    if p("scope_drift") > t["drift_flag_at"]:
        reasons.append("appears to have changed more than the task asked")
    if p("risk") >= t["risk_flag_at"]:
        reasons.append("a silent error here would do real damage")
    if diff and not read_only and p("diff_matches_task") < t["diff_match_at"]:
        reasons.append("the diff does not look like what the task asked for")

    # Deliberate: low stakes need no review even when verification is absent.
    if not reasons or stakes < t["stakes_at"]:
        return None

    # Use a different model family for changes spanning files.
    if p("touches_many_files") > t["many_files_at"]:
        who = _load_config()["roles"]["cross_family_reviewer"]["label"]
        why = ("spans several files and a different model family reviews it "
               "with a separate perspective")
    else:
        who = _load_config()["roles"]["fresh_reviewer"]["label"]
        why = "contained change; a clean second read is enough"

    return {
        "who": who,
        "why": why,
        "reasons": reasons,
        "confidence": min(stakes, max(1 - verified, p("scope_drift"),
                                      p("risk") / 2)),
        "raw": a,
    }
