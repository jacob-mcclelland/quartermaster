"""Offline checks for Codex usage parsing and the review fallback policy."""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from compression import zstd
except ImportError:  # Python before 3.14 has no zstd module
    zstd = None

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))


def _no_network(*args, **kwargs):
    raise AssertionError("network call attempted")


urllib.request.urlopen = _no_network

from jev import codex_usage, plan_router, review_gate  # noqa: E402
from jev.config import load as load_config  # noqa: E402

ROLES = load_config()["roles"]
CHEAP_MODEL = ROLES["cheap_worker"]["model"]
REVIEWER_MODEL = ROLES["cross_family_reviewer"]["model"]
CROSS_LABEL = ROLES["cross_family_reviewer"]["label"]
FRESH_LABEL = ROLES["fresh_reviewer"]["label"]


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError(f"{name}: {detail or 'condition did not hold'}")


def token_event(ts, primary=None, secondary=None, reached_type=None,
                has_credits=True):
    limits = {
        "limit_id": "codex",
        "plan_type": "team",
        "primary": primary,
        "secondary": secondary,
        "credits": {"has_credits": has_credits, "unlimited": False, "balance": None},
        "rate_limit_reached_type": reached_type,
    }
    return {"timestamp": ts, "type": "event_msg",
            "payload": {"type": "token_count", "info": {}, "rate_limits": limits}}


def window(used, minutes=300, reset=1790912095):
    return {"used_percent": used, "window_minutes": minutes, "resets_at": reset}


def write_rollout(root, name, event):
    folder = Path(root) / "2026" / "10" / "26"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    data = (json.dumps(event) + "\n").encode("utf-8")
    if name.endswith(".zst"):
        data = zstd.compress(data)
    path.write_bytes(data)
    return path


def usage_reading(used, now=None, **other):
    now = now or datetime.now(timezone.utc)
    reading = {
        "ts": now.isoformat(),
        "five_hour_used": used,
        "five_hour_resets_at": (now + timedelta(hours=1)).timestamp(),
        "weekly_used": 40.0,
        "weekly_resets_at": (now + timedelta(days=3)).timestamp(),
        "has_credits": True,
        "reached_type": None,
        "plan_type": "team",
        "limit_id": "codex",
        "source_file": "test-rollout",
    }
    reading.update(other)
    return reading


def check_latest_and_windows():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "sessions"
        older = token_event("2026-10-26T03:00:00Z", primary=window(20))
        newer = token_event("2026-10-26T04:00:00Z", primary=window(88))
        # The newer reading is compressed where the zstd module exists.
        newer_name = "rollout-b.jsonl.zst" if zstd else "rollout-b.jsonl"
        plain = write_rollout(root, "rollout-a.jsonl", older)
        second = write_rollout(root, newer_name, newer)
        # Make file modification order disagree with event order.
        os.utime(plain, (2, 2))
        os.utime(second, (1, 1))
        reading = codex_usage.latest(root)
        check("latest timestamp across rollout files",
              reading["ts"] == newer["timestamp"] and reading["five_hour_used"] == 88,
              repr(reading))
        check("newer source file", reading["source_file"] == str(second),
              reading.get("source_file"))

        mixed = token_event(
            "2026-10-26T05:00:00Z",
            primary=window(42, minutes=10080, reset=1791227791),
            secondary=window(75, minutes=300, reset=1790912095),
        )
        write_rollout(root, "rollout-c.jsonl", mixed)
        reading = codex_usage.latest(root)
        check("window identity follows duration",
              reading["five_hour_used"] == 75 and reading["weekly_used"] == 42
              and reading["five_hour_resets_at"] == 1790912095
              and reading["weekly_resets_at"] == 1791227791,
              repr(reading))


def check_fallback_thresholds():
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    reset = (now + timedelta(hours=1)).timestamp()
    base = {"ts": (now - timedelta(hours=1)).isoformat(),
            "five_hour_resets_at": reset, "reached_type": None}
    cases = [
        ("30 percent left", {**base, "five_hour_used": 70}, False),
        ("25 percent left", {**base, "five_hour_used": 75}, True),
        ("20 percent left", {**base, "five_hour_used": 80}, True),
        ("depleted credits", {**base, "five_hour_used": None,
                               "five_hour_resets_at": None,
                               "reached_type": "workspace_member_credits_depleted"}, True),
        ("credits false alone", {**base, "five_hour_used": 10,
                                  "has_credits": False}, False),
        ("window reset", {**base, "five_hour_used": 90,
                          "five_hour_resets_at": (now - timedelta(seconds=1)).timestamp()}, False),
        ("stale reading", {**base, "five_hour_used": 90,
                            "ts": (now - timedelta(hours=25)).isoformat()}, False),
        ("no reading", None, False),
    ]
    for name, reading, expected in cases:
        actual, reason = codex_usage.review_fallback(reading, now=now)
        check(name, actual is expected, reason)

    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "routes.json"
        config.write_text(json.dumps({"review_fallback_left_percent": 50}),
                          encoding="utf-8")
        previous = os.environ.get("AGENT_CHECKS_CONFIG")
        os.environ["AGENT_CHECKS_CONFIG"] = str(config)
        try:
            actual, reason = codex_usage.review_fallback(
                {**base, "five_hour_used": 60}, now=now)
        finally:
            if previous is None:
                os.environ.pop("AGENT_CHECKS_CONFIG", None)
            else:
                os.environ["AGENT_CHECKS_CONFIG"] = previous
        check("configured threshold applies", actual is True, reason)


def check_cache():
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "usage.json"
        empty = Path(tmp) / "sessions"
        saved = {key: os.environ.get(key) for key in
                 ("AGENT_CHECKS_CODEX_SESSIONS", "AGENT_CHECKS_CODEX_USAGE_FILE")}
        os.environ["AGENT_CHECKS_CODEX_SESSIONS"] = str(empty)
        os.environ["AGENT_CHECKS_CODEX_USAGE_FILE"] = str(cache)
        try:
            fresh = {**usage_reading(80),
                     "checked_at": datetime.now(timezone.utc).isoformat()}
            cache.write_text(json.dumps(fresh), encoding="utf-8")
            reading = codex_usage.cached()
            check("fresh cache is reused without a rollout",
                  reading is not None and reading["five_hour_used"] == 80
                  and "checked_at" not in reading, repr(reading))
            stale = {**fresh, "checked_at":
                     (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
            cache.write_text(json.dumps(stale), encoding="utf-8")
            check("stale cache is re-read from the rollouts",
                  codex_usage.cached() is None)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def digest():
    return {
        "goal": "Update the worker routing policy",
        "files": ["jev/plan_router.py"],
        "change": "Use the supplied usage signal to select an independent reviewer",
        "done_check": "The returned route names the expected reviewer",
        "depends_on_conversation": "nothing",
        "out_of_scope": "Other routing rules",
        "audience": "Maintainers",
        "reversibility": "The source changes can be reverted",
    }


class RouteClient:
    def ask(self, state, questions, label=None):
        values = {
            "work_type": {"value": "mechanical", "probabilities": {"mechanical": 1.0}},
            "brief_is_sufficient": {"value": 0.8},
            "dependencies_are_portable": {"value": 0.8},
            "complexity": {"value": 1.0},
            "stakes": {"value": 1.6},
            "needs_long_context": {"value": 0.0},
            "check_proves_done": {"value": 0.7},
            "splits_cleanly": {"value": 0.0},
        }
        return values


class GateClient:
    def ask(self, state, questions, label=None, **kwargs):
        values = {
            "was_verified": 0.1,
            "touches_many_files": 0.8,
            "user_facing": 0.9,
            "hard_to_reverse": 0.2,
            "scope_drift": 0.1,
            "risk": 1.8,
            "diff_matches_task": 0.3,
        }
        return {key: {"value": value} for key, value in values.items()
                if key in questions}


def check_routing_and_gate():
    on_usage = usage_reading(75)
    result = plan_router.route(digest(), RouteClient(), usage=on_usage)
    check("fallback route keeps the cheap worker", result["worker"] == "cheap_worker",
          result["worker"])
    check("route names the fresh-context reviewer",
          FRESH_LABEL in result["after"] and CROSS_LABEL not in result["after"],
          result["after"])
    check("route fallback metadata", result["review_fallback"]["on"] is True)

    off_usage = usage_reading(70)
    result = plan_router.route(digest(), RouteClient(), usage=off_usage)
    check("route keeps the cross-family reviewer above threshold",
          CROSS_LABEL in result["after"], result["after"])
    check("route fallback metadata off", result["review_fallback"]["on"] is False)

    gate_on = review_gate.assess_output(
        "Several files changed and the run did not verify the result.",
        client=GateClient(), diff="diff", usage=on_usage)
    check("gate chooses the fresh-context reviewer",
          gate_on["who"] == FRESH_LABEL, repr(gate_on))
    gate_off = review_gate.assess_output(
        "Several files changed and the run did not verify the result.",
        client=GateClient(), diff="diff", usage=off_usage)
    check("gate keeps the cross-family reviewer above threshold",
          gate_off["who"] == CROSS_LABEL, repr(gate_off))


FAKE_CODEX = r'''
import os, sys
from pathlib import Path
args = sys.argv[1:]
out = Path(args[args.index("-o") + 1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("fake Codex completed", encoding="utf-8")
Path(os.environ["FAKE_CODEX_MARKER"]).write_text("ran", encoding="utf-8")
'''


def dispatch_run(tmp, sessions, model, extra=()):
    folder = Path(tmp) / "launch"
    folder.mkdir(exist_ok=True)
    fake = Path(tmp) / "fake_codex.py"
    fake.write_text(FAKE_CODEX, encoding="utf-8")
    brief = Path(tmp) / "brief.txt"
    brief.write_text("Review the stated change.\n", encoding="utf-8")
    marker = Path(tmp) / "ran.marker"
    if marker.exists():
        marker.unlink()
    env = dict(os.environ,
               AGENT_CHECKS_CODEX_SESSIONS=str(sessions),
               AGENT_CHECKS_CODEX_USAGE_FILE=str(Path(tmp) / "usage-cache.json"),
               FAKE_CODEX_MARKER=str(marker))
    env.pop("TYPESAFE_API_KEY", None)
    python = sys.executable.replace("\\", "/")
    cmd = [sys.executable, str(SCRIPTS / "dispatch.py"),
           "--folder", str(folder), "--brief", str(brief),
           "--out", str(Path(tmp) / "out" / "result.txt"),
           "--no-judge", "--model", model, "--codex",
           f'"{python}" "{fake.as_posix()}"', *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          env=env, stdin=subprocess.DEVNULL, timeout=120)
    return proc, marker


def check_dispatch():
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        sessions = Path(tmp) / "sessions"
        write_rollout(sessions, "rollout-current.jsonl",
                      token_event(now.isoformat(), primary=window(
                          75, reset=(now + timedelta(hours=1)).timestamp())))

        proc, marker = dispatch_run(tmp, sessions, REVIEWER_MODEL)
        check("cross-family reviewer refused at 25 percent", proc.returncode == 3
              and FRESH_LABEL in proc.stdout and not marker.exists(),
              proc.stdout + proc.stderr)

        proc, marker = dispatch_run(tmp, sessions, CHEAP_MODEL)
        check("cheap worker dispatch continues", proc.returncode == 0 and marker.exists(),
              proc.stdout + proc.stderr)
        header = proc.stdout.find(f"=== WORKER ({CHEAP_MODEL}")
        usage_line = proc.stdout.find("Codex usage: 5-hour 75% used")
        check("usage printed after worker header",
              header != -1 and usage_line > header, proc.stdout)

        proc, marker = dispatch_run(tmp, sessions, REVIEWER_MODEL.upper())
        check("reviewer model match ignores case", proc.returncode == 3
              and not marker.exists(), proc.stdout + proc.stderr)

        proc, marker = dispatch_run(tmp, sessions, REVIEWER_MODEL, ["--force-reviewer"])
        check("force reviewer override", proc.returncode == 0 and marker.exists(),
              proc.stdout + proc.stderr)

        write_rollout(sessions, "rollout-current.jsonl",
                      token_event((now + timedelta(seconds=1)).isoformat(),
                                  primary=None, secondary=None,
                                  reached_type="workspace_member_credits_depleted"))
        proc, marker = dispatch_run(tmp, sessions, REVIEWER_MODEL)
        check("cross-family reviewer refused on depleted limit", proc.returncode == 3
              and FRESH_LABEL in proc.stdout and not marker.exists(),
              proc.stdout + proc.stderr)


def main():
    try:
        check_latest_and_windows()
        check_fallback_thresholds()
        check_cache()
        check_routing_and_gate()
        check_dispatch()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
