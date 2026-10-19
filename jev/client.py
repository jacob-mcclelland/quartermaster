"""Core Jev client.

Design notes:
  * Jev's numbers are NOT treated as calibrated probabilities. Everything
    here returns them as scores to sort by, never as odds to act on.
  * Option order can change answers, so `debias`
    runs every choice/score question a second time with the order reversed and
    averages. That doubles the input cost of those questions.
  * Every call is logged to JSONL. That log is the raw material for calibrating
    thresholds later, and for judging whether Jev is earning its place.
  * Do not ask Jev to do arithmetic, counting, or date math. Parse those in
    code and pass the result in words.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.typesafe.ai/v1/systemone"

# Pin the exact version. `jev-latest` moves under you and thresholds do not
# transfer across model versions.
from .config import load as _load_config
DEFAULT_MODEL = _load_config()["decision_client"]["model"]

# The local config supplies the input price for the call log.
INPUT_COST_PER_TOKEN = _load_config()["decision_client"]["input_cost_per_million"] / 1_000_000

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _current_log() -> Path:
    """One file per month.

    Not for disk, which is trivial. Calibration bands die when the model version
    or the question wording changes, and a month-stamped file makes it obvious
    which stretch of data belongs to which setup. A single file blending a
    before and an after is how you end up calibrating against two systems at
    once and not noticing.
    """
    from datetime import datetime, timezone

    return LOG_DIR / f"calls-{datetime.now(timezone.utc):%Y-%m}.jsonl"


class JevError(RuntimeError):
    """Any failure talking to Jev. Callers should treat this as 'no answer'."""


def _user_env_key() -> str | None:
    """Read TYPESAFE_API_KEY from the Windows user environment.

    A process inherits its environment at launch, so anything started before
    the variable was set (Claude Code, and therefore its hooks) cannot see
    it. Reading the registry directly avoids telling people to restart.
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "TYPESAFE_API_KEY")
            return value.strip() or None
    except Exception:  # noqa: BLE001 - not Windows, missing key, anything
        return None


# --- question builders -------------------------------------------------------

def noul(instructions: str, *, yes: str | None = None, no: str | None = None) -> dict:
    """A yes/no question. Returns a probability that the statement is true.

    Phrase `instructions` as a statement, not a question: Jev answers the
    literal wording. `yes`/`no` clarify the edges when the statement is fuzzy.
    """
    q: dict = {"type": "noul", "instructions": instructions}
    if yes is not None or no is not None:
        q["criteria"] = {"yes": yes or "", "no": no or ""}
    return q


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    """Pick one option. `criteria` maps option key -> what that option means.

    Always include an explicit 'other'/'none' option: without one, out-of-scope
    input gets a confidently wrong label.
    """
    if len(criteria) < 2:
        raise ValueError("choice needs at least 2 options")
    if len(criteria) > 255:  # verified against the API 2026-09-19
        raise ValueError(
            f"choice allows at most 255 options, got {len(criteria)}. "
            "For a bigger taxonomy, chain two Choice questions (coarse, then fine)."
        )
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def score(instructions: str, levels: list[str]) -> dict:
    """Rate on an ordered scale. `levels` are descriptions, lowest first."""
    if len(levels) < 2:
        raise ValueError("score needs at least 2 levels")
    if len(levels) > 10:  # verified against the API 2026-09-19
        raise ValueError(f"score allows at most 10 levels, got {len(levels)}")
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


# --- client ------------------------------------------------------------------

class Jev:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_MODEL,
        log_path: Path | str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY") or _user_env_key()
        if not self.api_key:
            raise JevError(
                "No API key. Set TYPESAFE_API_KEY in your environment "
                "(see README), or pass api_key=."
            )
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        # None = this month's file (the usual case); False = don't log.
        if log_path is False:
            self.log_path = None
        else:
            self.log_path = Path(log_path) if log_path else _current_log()

    # -- the one call you need
    def ask(
        self,
        state,
        questions: dict[str, dict],
        *,
        debias: bool = True,
        label: str | None = None,
    ) -> dict:
        """Ask Jev a set of typed questions about `state`.

        Returns {question_id: answer_dict}. Each answer carries its raw fields
        plus `value` (the headline number or label) and, when debiased,
        `order_flip`: True when reversing the option order changed the answer.
        A flip is a warning that the question wording is doing the work.
        """
        started = time.monotonic()
        first = self._post(state, questions)
        answers = dict(first["answers"])
        usage_in = first.get("usage", {}).get("input_tokens", 0)

        if debias:
            reversed_qs = {k: _reverse_options(q) for k, q in questions.items()}
            # Compare serialised form, not the dicts: {"a":1,"b":2} equals
            # {"b":2,"a":1} in Python, so a plain != would silently skip the
            # second pass whenever every question was a Choice.
            if json.dumps(reversed_qs) != json.dumps(questions):
                second = self._post(state, reversed_qs)
                usage_in += second.get("usage", {}).get("input_tokens", 0)
                answers = _merge(answers, second["answers"])

        for qid, ans in answers.items():
            ans["value"] = _headline(ans)
            ans.setdefault("order_flip", None)  # None = not debiased

        elapsed = time.monotonic() - started
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "model": self.model,
            "questions": {k: _fingerprint(v) for k, v in questions.items()},
            "answers": answers,
            "input_tokens": usage_in,
            "cost_usd": round(usage_in * INPUT_COST_PER_TOKEN, 8),
            "seconds": round(elapsed, 3),
            "debiased": debias,
        }
        self._log(record)
        return answers

    # -- plumbing
    def _post(self, state, questions: dict) -> dict:
        body = json.dumps(
            {"model": self.model, "state": state, "questions": questions}
        ).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        delay = 1.0
        last = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                if e.code in (429, 529):  # throttled / overloaded: back off
                    last = JevError(f"HTTP {e.code}: {detail}")
                    time.sleep(delay)
                    delay *= 2
                    continue
                if e.code == 401:
                    raise JevError("HTTP 401: API key rejected.") from e
                if e.code == 422:
                    raise JevError(f"HTTP 422: malformed question. {detail}") from e
                raise JevError(f"HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last = JevError(f"Network error: {e}")
                time.sleep(delay)
                delay *= 2
        raise last or JevError("Request failed.")

    def _log(self, record: dict) -> None:
        if not self.log_path:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            pass  # logging must never break the caller


# --- helpers -----------------------------------------------------------------

def _reverse_options(q: dict) -> dict:
    """Reverse option/level order so we can measure order sensitivity."""
    if q["type"] == "choice":
        return {**q, "criteria": dict(reversed(list(q["criteria"].items())))}
    if q["type"] == "score":
        return {**q, "criteria": list(reversed(q["criteria"]))}
    return q  # noul has no order to reverse


def _merge(first: dict, second: dict) -> dict:
    """Average the two orderings; flag any answer that changed."""
    out = {}
    for qid, a in first.items():
        b = second.get(qid)
        if not b or a.get("type") != b.get("type"):
            out[qid] = a
            continue
        t = a["type"]
        if t == "noul":
            out[qid] = {**a, "noul": (a["noul"] + b["noul"]) / 2, "order_flip": False}
        elif t == "choice":
            probs = {
                k: (a.get("probabilities", {}).get(k, 0.0)
                    + b.get("probabilities", {}).get(k, 0.0)) / 2
                for k in a.get("probabilities", a["criteria"] if "criteria" in a else {})
            }
            best = max(probs, key=probs.get) if probs else a.get("choice")
            out[qid] = {
                **a,
                "choice": best,
                "probabilities": probs,
                "confidence": (a.get("confidence", 0) + b.get("confidence", 0)) / 2,
                "order_flip": a.get("choice") != b.get("choice"),
            }
        elif t == "score":
            # b's levels were reversed, so its score counts down from the top.
            n = len(a.get("legend", {})) or len(a.get("probabilities", {}))
            b_score = (n - 1) - b["score"] if n else b["score"]
            out[qid] = {
                **a,
                "score": (a["score"] + b_score) / 2,
                "confidence": (a.get("confidence", 0) + b.get("confidence", 0)) / 2,
                "order_flip": abs(a["score"] - b_score) > 0.5,
            }
        else:
            out[qid] = a
    return out


def _headline(ans: dict):
    t = ans.get("type")
    if t == "noul":
        return ans.get("noul")
    if t == "choice":
        return ans.get("choice")
    if t == "score":
        return ans.get("score")
    return None


def _fingerprint(q: dict) -> dict:
    """Log the question's shape and a hash of its wording.

    Wording changes move answers, so a threshold tuned on one wording is void
    once the text changes. The hash is how we detect that later.
    """
    text = json.dumps(q, sort_keys=True)
    return {
        "type": q["type"],
        "hash": hashlib.sha256(text.encode()).hexdigest()[:12],
        "n_options": len(q.get("criteria", [])) if q["type"] != "noul" else None,
    }
