"""Every threshold the checks decide on, each labelled with its basis.

  DERIVED    follows from what each kind of error costs; the arithmetic is shown.
  UNFOUNDED  a guess, left visible rather than dressed up.

Nothing here is calibrated. Calibration needs labelled outcomes, and these
checks do not record any yet. A DERIVED number is an argument, not a
measurement.

For a calibrated probability, the cost-optimal cut is

    C_false_positive / (C_false_positive + C_false_negative)

so the cheaper a false positive, the lower the bar. That formula assumes a
calibration these scores do not have. This ledger also treats a yes/no score
near 0.5 as ambiguous rather than moderate, so a cut meant to act only on a
clear answer sits at 0.6 or above.

Scales: yes/no scores run 0 to 1; `complexity` runs 0 to 3; `stakes` and
`risk` run 0 to 2.

A JSON file at logs/thresholds.json, shaped {"values": {...}}, overrides
these values locally.
"""

from __future__ import annotations

import json
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "logs" / "thresholds.json"

DEFAULTS = {
    # --- router (jev/plan_router.py) ---------------------------------------

    # DERIVED. A wrong delegation costs a cheap-worker run plus a retry; a
    # wrong keep spends the orchestrator's scarcer budget. Call that 1 to 2,
    # giving 1/(1+2) = 0.33. Held at 0.45 on the assumption that cheap
    # workers finish fewer whole hard tasks; how far above 0.33 is a judgement.
    "delegate_at": 0.45,

    # UNFOUNDED. Design or investigation work stays with the orchestrator.
    # Probability mass below the top choice carries signal, which justifies
    # reading the distribution; it does not justify 0.30 as the cut.
    "design_mass_block": 0.30,

    # UNFOUNDED. Above the ambiguous middle; 0.60 rather than 0.65 is a guess.
    "long_context_at": 0.60,

    # UNFOUNDED. On the 0 to 3 complexity scale, 2.2 sits just above
    # "several interacting parts". Work above it stays with the orchestrator.
    "keep_complexity_at": 2.2,

    # UNFOUNDED. On the 0 to 2 stakes scale, 1.4 is most of the way to
    # "wrong output reaches someone outside". Above it, design work gets a
    # consult first and other work gets a cross-family review.
    "high_stakes_at": 1.4,

    # UNFOUNDED. Below this, the done-check is not trusted to prove
    # completion, and a review is planned instead.
    "done_check_at": 0.60,

    # UNFOUNDED. Fanning out wrongly wastes a round of parallel work; missing
    # a fan-out costs only wall-clock time. That asymmetry argues for a LOW
    # bar, and 0.70 is high, so this is probably wrong in the cautious
    # direction.
    "fanout_at": 0.70,

    # --- review gate (jev/review_gate.py) ----------------------------------

    # UNFOUNDED. Work is passed without review when the record clearly shows
    # a check ran (above this), nothing drifted, and the diff matches.
    "verified_pass_at": 0.65,
    "drift_pass_below": 0.40,
    "diff_match_at": 0.50,

    # UNFOUNDED. Reasons to review: clearly unverified, drifted, risky.
    "unverified_below": 0.40,
    "drift_flag_at": 0.50,
    "risk_flag_at": 1.5,

    # UNFOUNDED as a value. The direction is reasoned: a stakes gate must
    # clear the ambiguous middle. Why 0.70 and not 0.65 or 0.80 is a guess.
    "stakes_at": 0.70,

    # UNFOUNDED. Above this, the change spans files and goes to a reviewer
    # from a different model family.
    "many_files_at": 0.50,

    # --- brief linter (hooks/brief_lint.py) --------------------------------

    # UNFOUNDED. A spec check below this is reported as missing.
    "brief_spec_at": 0.50,
    # UNFOUNDED. Above this, the brief is treated as research and judged on
    # the shape of its answer instead of a test command.
    "brief_research_at": 0.60,
    # UNFOUNDED. Above this, the brief is flagged as a poor fit for a cheap
    # worker.
    "brief_escalate_at": 0.60,
}


def load() -> dict:
    """Current thresholds: the local override file if present, else the defaults."""
    try:
        stored = json.loads(STORE.read_text(encoding="utf-8"))
        return {**DEFAULTS, **stored.get("values", {})}
    except Exception:  # noqa: BLE001
        return dict(DEFAULTS)
