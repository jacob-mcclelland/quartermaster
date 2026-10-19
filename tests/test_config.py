"""Offline tests for config loading: a partial local file keeps the example's
other values, and a broken one is reported and skipped instead of crashing
every hook.
"""

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jev import config  # noqa: E402


def check(name, cond, detail=""):
    if not cond:
        print(f"FAIL: {name} {detail}")
        raise SystemExit(1)


def load_with(text: str | None):
    """Load config with AGENT_CHECKS_CONFIG pointing at a file holding `text`."""
    old = os.environ.get("AGENT_CHECKS_CONFIG")
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "routes.json"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        os.environ["AGENT_CHECKS_CONFIG"] = str(path)
        try:
            with contextlib.redirect_stderr(err):
                return config.load(), err.getvalue()
        finally:
            if old is None:
                os.environ.pop("AGENT_CHECKS_CONFIG", None)
            else:
                os.environ["AGENT_CHECKS_CONFIG"] = old


def main() -> int:
    example, _ = load_with('{}')
    check("empty local file gives the example", example["workers_have_network"] is True, example)

    merged, err = load_with('{"roles": {"cheap_worker": {"model": "my-model"}}}')
    check("partial override applies", merged["roles"]["cheap_worker"]["model"] == "my-model", merged)
    check("partial override keeps sibling keys", merged["roles"]["cheap_worker"]["effort"] == "xhigh", merged)
    check("partial override keeps other roles", "orchestrator" in merged["roles"], merged)
    check("no warning for a good file", err == "", err)

    for bad in ('{"roles": ', '[1, 2]'):
        loaded, err = load_with(bad)
        check(f"broken file falls back: {bad!r}", loaded == example, loaded)
        check(f"broken file is reported: {bad!r}", "ignoring config" in err, err)

    loaded, err = load_with(None)
    check("missing named file falls back", loaded == example and "ignoring config" in err, err)
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
