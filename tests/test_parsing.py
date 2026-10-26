"""Offline tests for the regular expressions the checks depend on.

Each check reads text it does not control: a shell command, an npm shim, a
research report. If one of these patterns breaks, the check does not fail; it
goes quiet. A text clean-up once turned every `?` in these files into `:`,
and the rest of the suite still passed. These tests pin the parsing itself.
"""

import importlib.util
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from jev import judge  # noqa: E402
import dispatch  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brief_lint = _load("brief_lint", ROOT / "hooks" / "brief_lint.py")


def check(name, cond, detail=""):
    if not cond:
        print(f"FAIL: {name} {detail}")
        raise SystemExit(1)


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_brief_extraction(tmp: Path) -> None:
    text = "Edit src/parser.py so empty input returns []. Run the tests after."
    spaced = tmp / "dir with spaces" / "brief one.txt"
    spaced.parent.mkdir()
    spaced.write_text(text, encoding="utf-8")
    plain = tmp / "brief.txt"
    plain.write_text(text, encoding="utf-8")

    check("agent prompt",
          brief_lint.extract_brief({"tool_name": "Agent", "tool_input": {"prompt": text}}) == text)
    check("dispatch, double-quoted path with spaces",
          brief_lint.extract_brief(bash(f'python scripts/dispatch.py --brief "{spaced}" --out r.txt')) == text)
    check("dispatch, single-quoted path",
          brief_lint.extract_brief(bash(f"python scripts/dispatch.py --brief '{spaced}' --out r.txt")) == text)
    check("dispatch, bare path",
          brief_lint.extract_brief(bash(f"python scripts/dispatch.py --brief {plain} --out r.txt")) == text)
    check("raw exec, brief read with cat",
          brief_lint.extract_brief(bash(f"codex exec \"$(cat '{plain}')\"")) == text)
    check("raw exec, inline brief",
          brief_lint.extract_brief(bash(f'codex exec -m some-model "{text}"')) == text)
    check("unrelated command", brief_lint.extract_brief(bash("git status")) is None)

    for command in ("codex exec x", "codex.cmd exec x", "cd repo; codex exec x", "(codex exec x)"):
        check(f"raw exec detected: {command}", brief_lint.RAW_EXEC.search(command) is not None)
    for command in ("mycodex exec x", "codex review x"):
        check(f"not raw exec: {command}", brief_lint.RAW_EXEC.search(command) is None)


def test_windows_shim(tmp: Path) -> None:
    # An npm shim runs `node "%dp0%\<script>.js"`; cmd.exe cuts batch-file
    # arguments at the first newline, so dispatch runs node directly.
    shim_dir = tmp / "npm"
    shim_dir.mkdir()
    (shim_dir / "codex.js").write_text("// entry point\n", encoding="utf-8")
    node = shim_dir / "node.exe"
    node.write_text("", encoding="utf-8")
    shim = shim_dir / "codex.cmd"
    shim.write_text('@ECHO off\r\n"%_prog%" "%dp0%\\codex.js" %*\r\n', encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    resolved = dispatch.resolve_codex([str(shim), "--flag"])
    check("shim resolves to node plus script",
          resolved == [str(node), str(shim_dir / "codex.js"), "--flag"], repr(resolved))


def test_report_sources() -> None:
    report = "\n".join([
        "- Judges barely beat chance at spotting false success https://example.com/paper.",
        "- See [the guide](http://example.org/guide) for how options were reversed in testing.",
        "- Too short https://example.net/x",
        "No link on this line at all, so it is not a cited claim.",
    ])
    cited = judge.extract_cited_claims(report)
    # Changed expectation: link-only URLs remain in the cited URL set.
    check("https and http both found",
          set(cited) == {"https://example.com/paper", "http://example.org/guide",
                         "https://example.net/x"}, repr(cited))
    # Changed expectation: a link-only URL has no claim text.
    check("link-only URL has no claims", cited.get("https://example.net/x") == [])
    check("trailing punctuation dropped from URL", "https://example.com/paper" in cited)
    check("claim text kept without the URL",
          cited["https://example.com/paper"] == ["Judges barely beat chance at spotting false success"],
          repr(cited["https://example.com/paper"]))

    html = ("<html><style>p{}</style><script>var x = 1;</script><!-- note -->"
            "<p>Visible &amp; kept</p></html>")
    check("html stripped", judge._strip_html(html) == "Visible & kept", repr(judge._strip_html(html)))

    fetched = []
    original = judge.fetch_url
    judge.fetch_url = lambda url, timeout=20: (fetched.append(url) or ("page text", None))
    try:
        check("URL source is fetched", judge.load_source("https://example.com/a") == ("page text", None))
        check("plain text is taken as text", judge.load_source("not a url") == ("not a url", None))
    finally:
        judge.fetch_url = original
    check("fetch called once", fetched == ["https://example.com/a"], repr(fetched))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        test_brief_extraction(Path(tmp))
        test_windows_shim(Path(tmp))
    test_report_sources()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
