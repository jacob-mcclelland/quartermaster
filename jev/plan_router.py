"""Route a DIGESTED task, never raw text.

The first version (observed in this project's own use, 2026-09-20) forwarded
the user's words to Jev and failed completely: the user's prompts were
conversational shorthand ("do it", "fix the three gaps"), so the state was a
well-labelled empty box. Over one session it said "delegate" on 0 of 11 real
prompts while about 1.5M tokens of subagent work ran on the expensive tier.

A router in front of an API can send the raw message, because there the
message is the full task. Inside a long conversation it is not. So here the
orchestrating model digests first: it breaks the request into a structured
statement of the work, and only that digest goes to Jev, with a few specific
questions.

The division of labour that keeps this honest:

  * The agent supplies FACTS: which files, what changes, what proves it is done,
    what it depends on. Checkable, and visible to the user.
  * Jev supplies JUDGEMENT: complexity, briefability, stakes, verifiability.
  * Code supplies POLICY: thresholds and the final call.

The agent must never supply the judgement. If the digest could say "this is
simple", the author of the digest would be answering their own question, so
characterising words are rejected before anything is sent.

The author of a digest does not decide, predict, or record where the work
should go, and disagreeing with a route is not a valid reason to veto it.
"""

from __future__ import annotations

import os
import posixpath
import re

from .client import Jev, choice, noul, score
from .config import load as _load_config
from .thresholds import load as _load_thresholds

REQUIRED = ("goal", "files", "change", "done_check", "depends_on_conversation",
            "out_of_scope", "audience", "reversibility")


def _comparable(path: str) -> str:
    """Expand ~, use forward slashes, drop ./ and .. segments, lowercase."""
    path = os.path.expanduser(path).replace("\\", "/")
    return posixpath.normpath(path).lower()


def unreachable_files(files) -> list[str]:
    """Return paths a dispatched worker cannot write, preserving their originals.

    Only literal paths are compared: a relative entry is never resolved
    against the current folder, and symlinks are not followed.
    """
    protected = [_comparable(folder) for folder in _load_config()["protected_paths"]]
    unreachable = []
    for entry in files:
        if not isinstance(entry, str):
            continue
        if entry.startswith(("http://", "https://")):
            continue
        comparable = _comparable(entry)
        if any(comparable == folder or comparable.startswith(folder + "/")
               for folder in protected):
            unreachable.append(entry)
    return unreachable

# Words that smuggle a conclusion into a field that should hold a fact.
JUDGEMENT_WORDS = re.compile(
    r"\b(simple|simply|easy|easily|trivial|trivially|straightforward|quick|"
    r"quickly|just|merely|obvious|obviously|complex|complicated|hard|"
    r"difficult|tricky|risky|safe|minor|major|small|tiny|huge)\b",
    re.IGNORECASE,
)

ROUTING_OUTCOME_FIELDS = {
    "expected_route",
    "expected_worker",
    "predicted_route",
    "predicted_worker",
    "suggested_worker",
    "preferred_worker",
    "my_guess",
    "should_go_to",
    "route_to",
    "worker",
}

QUESTIONS = {
    "work_type": choice(
        "What type of work does this digest describe",
        {
            "mechanical": "Carrying out a settled change: edits, renames, "
                          "boilerplate, tests for existing code.",
            "contained_logic": "Writing or changing logic inside a bounded "
                               "area, with the approach already decided.",
            "investigation": "Finding out why something happens or what the "
                             "current state is; the approach is not settled.",
            "design": "Choosing an approach, weighing options, or reviewing "
                      "work for quality.",
            "research": "Gathering information from outside sources and "
                        "reporting it.",
            "other": "None of these.",
        },
    ),
    "complexity": score(
        "How much reasoning does completing this work require",
        [
            "Follow the stated change; no decisions to make.",
            "A few local decisions inside the named files.",
            "Several interacting parts that must be reasoned about together.",
            "Deep reasoning across many constraints at once.",
        ],
    ),
    "needs_long_context": noul(
        "Completing this work requires holding a large amount of source material "
        "in mind at once: reading a long document, transcript, or many files "
        "end to end, rather than working within a few named places."
    ),
    "brief_is_sufficient": noul(
        "A competent worker who has never seen the conversation could complete "
        "this work from the digest alone, using the listed files."
    ),
    "dependencies_are_portable": noul(
        "Whatever this work depends on from the conversation is stated in the "
        "digest specifically enough to be copied into a written brief."
    ),
    "check_proves_done": noul(
        "The stated done-check would actually show the work is complete and "
        "correct, rather than only that something ran."
    ),
    "stakes": score(
        "If this work were wrong and nobody noticed, how bad would it be, "
        "given the stated audience and reversibility",
        [
            "No consequence; fixed whenever noticed.",
            "Some rework for the people on this machine.",
            "Wrong output reaches someone outside, or is costly to undo.",
        ],
    ),
    "splits_cleanly": noul(
        "The listed pieces could be worked on at the same time by separate "
        "workers without needing each other's results."
    ),
}


def validate(digest: dict) -> list[str]:
    """Problems with the digest itself. Empty list means it may be sent."""
    problems = []
    for field in REQUIRED:
        if not str(digest.get(field, "")).strip():
            problems.append(f"missing field: {field}")
    for field, value in digest.items():
        if str(field).casefold() in ROUTING_OUTCOME_FIELDS:
            problems.append(
                f"{field} states an expected destination; the destination is "
                "the router's to decide, not the digest author's"
            )
        text = " ".join(value) if isinstance(value, list) else str(value)
        hits = sorted({m.group(0).lower() for m in JUDGEMENT_WORDS.finditer(text)})
        if hits:
            problems.append(
                f"{field} contains judgement words {hits}; state facts and "
                "let Jev judge"
            )
    return problems


def route(digest: dict, client: Jev | None = None) -> dict:
    """Ask Jev the specific questions and apply policy.

    Returns {worker, effort, after, fanout, reasons, answers}.
    Worker values are role keys from the local configuration.
    """
    problems = validate(digest)
    if problems:
        raise ValueError("digest rejected: " + "; ".join(problems))

    client = client or Jev()
    t = _load_thresholds()
    config = _load_config()
    roles = config["roles"]
    a = client.ask(digest, QUESTIONS, label="route_plan")

    def p(k: str) -> float:
        return a.get(k, {}).get("value") or 0.0

    kind = a.get("work_type", {}).get("value")
    kind_p = a.get("work_type", {}).get("probabilities", {}) or {}
    reasons: list[str] = []

    portable = min(p("brief_is_sufficient"), p("dependencies_are_portable"))
    complexity = p("complexity")            # 0..3
    stakes = p("stakes")                    # 0..2
    design_mass = kind_p.get("design", 0.0) + kind_p.get("investigation", 0.0)

    # --- who does the work
    if kind in ("design", "investigation") or design_mass >= t["design_mass_block"]:
        worker, effort = "orchestrator", "default"
        reasons.append(f"{kind} work: the approach is the job "
                       f"(design+investigation mass {design_mass:.2f})")
        if kind == "design" and stakes >= t["high_stakes_at"]:
            worker = "consult_then_orchestrator"
            reasons.append("direction-setting with outside consequences; "
                           "consult on the approach first")
    elif portable < t["delegate_at"]:
        worker, effort = "orchestrator", "default"
        reasons.append(f"not portable as stated ({portable:.2f}); tighten "
                       "the digest or keep it")
    elif p("needs_long_context") > t["long_context_at"]:
        worker, effort = "long_context_worker", roles["long_context_worker"]["effort"]
        reasons.append("long input requires a long-context worker")
    elif complexity >= t["keep_complexity_at"]:
        worker, effort = "orchestrator", "default"
        reasons.append(f"complexity {complexity:.1f}/3 exceeds the cheap worker band")
    else:
        worker, effort = "cheap_worker", roles["cheap_worker"]["effort"]
        reasons.append(f"portable ({portable:.2f}), complexity {complexity:.1f}/3")

    # A portable research brief can be dispatched when workers have network.
    # Research runs one effort step below implementation.
    if (kind == "research" and worker == "orchestrator"
            and portable >= t["delegate_at"] and config["workers_have_network"]):
        worker, effort = "cheap_worker", "high"
        reasons.append("research has a portable brief and workers have network access")
    if kind == "research" and not config["workers_have_network"] and worker == "cheap_worker":
        worker, effort = "orchestrator", "default"
        reasons.append("research needs network access that workers do not have")

    blocked_files = unreachable_files(digest.get("files") or [])
    if worker in ("cheap_worker", "long_context_worker") and blocked_files:
        worker, effort = "orchestrator", "default"
        paths = ", ".join(repr(path) for path in blocked_files)
        reasons.append(
            f"The named paths {paths} are protected, so the task stays in session"
        )

    # --- what happens after
    after = None
    if worker in ("cheap_worker", "long_context_worker"):
        if p("check_proves_done") >= t["done_check_at"]:
            after = "run the done-check yourself; its exit code decides"
        else:
            after = ("done-check is weak; strengthen it before dispatch, or "
                     "plan a review of the result")
            reasons.append(f"done-check only {p('check_proves_done'):.2f} "
                           "likely to prove completion")
    if stakes >= t["high_stakes_at"] and worker != "consult_then_orchestrator":
        reviewer = roles["cross_family_reviewer"]
        after = ((after + "; then ") if after else "") + \
            f"{reviewer['label']} at {reviewer.get('effort', 'default')} effort " \
            f"before it ships (stakes {stakes:.1f}/2)"

    fanout = p("splits_cleanly") >= t["fanout_at"] and len(digest.get("pieces") or []) >= 2

    return {
        "worker": worker,
        "effort": effort,
        "after": after,
        "fanout": fanout,
        "reasons": reasons,
        "answers": {k: v.get("value") for k, v in a.items()},
    }
