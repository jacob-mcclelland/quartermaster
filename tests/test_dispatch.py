"""Offline tests for scripts/dispatch.py. No network, no API key, no Codex.

A temporary git repo stands in for the launch folder, with edits that
"another agent" made before the dispatch. A fake codex records what it was
given and changes two files. The dispatcher must report only the worker's
changes, pass a multi-line brief through intact, and leave stdin closed.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "scripts"))


def _no_network(*args, **kwargs):
    raise AssertionError("network call attempted")


urllib.request.urlopen = _no_network

import dispatch  # noqa: E402
from jev import client as jev_client  # noqa: E402
from jev import judge  # noqa: E402

FAKE_CODEX = r'''
import json, os, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
folder = args[args.index("-C") + 1]
record = {"args": args, "brief": args[-1], "stdin": sys.stdin.read()}
with open(os.environ["FAKE_CODEX_LOG"], "w", encoding="utf-8") as fh:
    json.dump(record, fh)
for rel, text in json.loads(os.environ["FAKE_CODEX_WRITES"]).items():
    with open(os.path.join(folder, rel), "w", encoding="utf-8") as fh:
        fh.write(text)
with open(out, "w", encoding="utf-8") as fh:
    fh.write("Done. Ran the tests: 3 passed.")
'''


def check(name, cond, detail=""):
    if not cond:
        print(f"FAIL: {name} {detail}")
        raise SystemExit(1)


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


def run_dispatch(repo, tmp, writes, extra=()):
    fake = Path(tmp) / "fake_codex.py"
    fake.write_text(FAKE_CODEX, encoding="utf-8")
    brief = Path(tmp) / "brief.txt"
    brief_text = ('Edit a.txt.\nSecond line with "quotes" & a pipe | and %PATH%.\n'
                  "Other agents are working in this tree concurrently.")
    brief.write_text(brief_text, encoding="utf-8")
    log = Path(tmp) / "fake_log.json"
    env = dict(os.environ, FAKE_CODEX_LOG=str(log),
               FAKE_CODEX_WRITES=json.dumps(writes))
    env.pop("TYPESAFE_API_KEY", None)
    py = sys.executable.replace("\\", "/")
    cmd = [sys.executable, str(HERE.parent / "scripts" / "dispatch.py"), "--folder", repo,
           "--brief", str(brief), "--out", str(Path(tmp) / "out" / "result.txt"),
           "--no-judge", "--codex", f"{py} {fake.as_posix()}", *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          env=env, stdin=subprocess.DEVNULL, timeout=120)
    record = json.loads(log.read_text(encoding="utf-8")) if log.exists() else {}
    return proc, record, brief_text


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
        for name in ("a.txt", "b.txt", "c.txt"):
            Path(repo, name).write_text(f"{name} original\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "init")
        # Another agent's work, present before the dispatch.
        Path(repo, "b.txt").write_text("b.txt edited by another agent\n", encoding="utf-8")
        Path(repo, "theirs.txt").write_text("untracked, not ours\n", encoding="utf-8")

        proc, record, brief_text = run_dispatch(
            repo, tmp, {"a.txt": "a.txt changed by worker\n", "new.txt": "created\n"})
        out = proc.stdout
        check("exit code", proc.returncode == 0, proc.stdout + proc.stderr)
        check("brief passed intact", record.get("brief") == brief_text, repr(record.get("brief")))
        check("stdin closed", record.get("stdin") == "")
        check("approve-for-me without --sandbox",
              "--approve-for-me" in record["args"] and "--sandbox" not in record["args"])
        check("model and effort passed",
              "your-cheap-model" in record["args"] and "model_reasoning_effort=xhigh" in record["args"])
        check("result printed", "3 passed" in out)
        check("worker files listed", "a.txt  +1 -1" in out and "new.txt  (new file" in out, out)
        check("other agent's files not listed", "b.txt" not in out and "theirs.txt" not in out, out)
        check("count", "FILES THE WORKER CHANGED (2)" in out, out)

        # The worker edits a file someone else had already changed.
        proc, record, _ = run_dispatch(
            repo, tmp, {"b.txt": "b.txt edited again by worker\n"}, extra=["--read-only"])
        out = proc.stdout
        check("pre-modified file listed with warning",
              "b.txt" in out and "already modified before dispatch" in out, out)
        check("read-only uses sandbox flag",
              "--sandbox" in record["args"] and "--approve-for-me" not in record["args"])

        # Non-git folder: falls back to size and mtime.
        plain = os.path.join(tmp, "plain")
        os.makedirs(plain)
        Path(plain, "keep.txt").write_text("x", encoding="utf-8")
        proc, _, _ = run_dispatch(plain, tmp, {"made.txt": "y"})
        check("non-git folder diff", "made.txt" in proc.stdout and "keep.txt" not in proc.stdout,
              proc.stdout)

    # --- the gate, in-process with a fake client
    JevError = jev_client.JevError

    class FakeClient:
        def __init__(self, answers=None, fail=None):
            self.answers, self.fail, self.calls = answers or {}, fail, []

        def ask(self, state, questions, label=None, **kw):
            if self.fail:
                raise self.fail
            self.calls.append((state, questions))
            return {k: {"value": v} for k, v in self.answers.items() if k in questions}

    needs_review = {"was_verified": 0.1, "touches_many_files": 0.8, "user_facing": 0.9,
                    "hard_to_reverse": 0.2, "scope_drift": 0.1, "risk": 1.8,
                    "diff_matches_task": 0.3}
    client = FakeClient(needs_review)
    text = dispatch.run_gate("brief", "done", ["  a.txt  +1 -1"], "--- a/a.txt\n+++ b/a.txt", client=client)
    state, questions = client.calls[0]
    check("gate sees the diff", state.get("diff", "").startswith("--- a/a.txt"))
    check("gate asks whether the diff matches", "diff_matches_task" in questions)
    check("gate verdict", text.startswith("REVIEW: send to a reviewer from a different model family") and "does not look like" in text, text)

    client = FakeClient(needs_review)
    dispatch.run_gate("brief", "done", [], "", client=client)
    check("no diff, no diff question", "diff_matches_task" not in client.calls[0][1])

    clean = {"was_verified": 0.9, "scope_drift": 0.1, "diff_matches_task": 0.9}
    check("verified work passes",
          dispatch.run_gate("b", "r", [], "d", client=FakeClient(clean)) == "no review needed")
    text = dispatch.run_gate("b", "r", [], "d", client=FakeClient(fail=JevError("No API key.")))
    check("gate fails open on an outage", text.startswith("review gate unavailable"), text)
    text = dispatch.run_gate("b", "r", [], "d", client=FakeClient(fail=NameError("name 'x' is not defined")))
    check("gate names a bug as a bug", text.startswith("review gate CRASHED (NameError"), text)

    # --- the source check, with no key: an outage, never a crash.
    # Runs the real path (judge -> file_ask -> client) with only the fetch and
    # the key lookup replaced, so a broken import in that chain fails here.
    env_key = os.environ.pop("TYPESAFE_API_KEY", None)
    original_fetch, original_user_key = judge.fetch_url, jev_client._user_env_key
    judge.fetch_url = lambda url, timeout=20: ("page text about the claim", None)
    jev_client._user_env_key = lambda: None
    try:
        text = dispatch.run_source_check(
            "- Option order can change a model's answer https://example.com/paper")
    finally:
        judge.fetch_url, jev_client._user_env_key = original_fetch, original_user_key
        if env_key is not None:
            os.environ["TYPESAFE_API_KEY"] = env_key
    check("source check without a key is an outage, not a crash",
          text.startswith("scanned: final message")
          and len(text.splitlines()) > 1
          and text.splitlines()[1].startswith("source check unavailable")
          and "API key" in text, text)

    print("PASS")


if __name__ == "__main__":
    main()
