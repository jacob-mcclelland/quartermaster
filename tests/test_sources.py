"""Offline tests for research URL extraction and dispatch source checks."""

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

from jev import judge  # noqa: E402
import dispatch  # noqa: E402


def check(name, cond, detail=""):
    if not cond:
        print(f"FAIL: {name} {detail}")
        raise SystemExit(1)


def ask(state, questions, label):
    answers = {"injection": {"value": 0.0}, "substance": {"value": 1.0}}
    for key in questions:
        if key.startswith("claim_"):
            answers[key] = {"value": "supports",
                            "probabilities": {"supports": 1.0}}
    return answers


def with_pages(pages, callback):
    original = judge.fetch_url

    def fake_fetch(url, timeout=20):
        if url in pages:
            return pages[url], None
        return None, "fetch failed: 404"

    judge.fetch_url = fake_fetch
    try:
        return callback()
    finally:
        judge.fetch_url = original


def main():
    url = "https://example.com/careers"
    extracted = judge.extract_cited_claims(
        "- [Employer career page](https://example.com/careers)")
    check("markdown link label creates uncited URL", extracted == {url: []}, extracted)

    raw_url = "https://raw.githubusercontent.com/a/b/main/file.js"
    extracted = judge.extract_cited_claims(
        "- [`file.js`:L1-L17](https://raw.githubusercontent.com/a/b/main/file.js)")
    check("code link label creates uncited URL", extracted == {raw_url: []}, extracted)

    extracted = judge.extract_cited_claims(
        "The role pays $90k to $110k per the "
        "[Employer career page](https://example.com/careers).")
    check("markdown URL is replaced with its label",
          extracted[url] == ["The role pays $90k to $110k per the Employer career page."])

    extracted = judge.extract_cited_claims(
        "The role pays $90k to $110k, see <https://example.com/careers>.")
    claim = extracted[url][0]
    check("angle URL is found and removed from claim",
          claim.startswith("The role pays $90k to $110k")
          and "<" not in claim and ">" not in claim, claim)
    check("dangling angle-link wording is cleaned", claim == "The role pays $90k to $110k.",
          claim)

    extracted = judge.extract_cited_claims("<https://example.com/careers>")
    check("angle-only URL creates uncited source", extracted == {url: []}, extracted)

    extracted = judge.extract_cited_claims(
        "The role pays $90k to $110k https://example.com/careers")
    check("bare URL is removed from claim",
          extracted[url] == ["The role pays $90k to $110k"])

    about = "https://example.com/about"
    extracted = judge.extract_cited_claims(
        "Sources:\n1. [Careers](https://example.com/careers)\n"
        "2. https://example.com/about")
    check("sources list URLs remain uncited", extracted == {url: [], about: []}, extracted)

    mixed = "https://example.com/mixed"
    report = "\n".join([
        f"The employer lists software roles here: [Careers]({mixed})",
        f"The salary range appears in this official posting: <{mixed}>",
        f"The company's office location appears in its directory {mixed}",
        f"The employer lists software roles here: [Careers]({mixed})",
    ])
    extracted = judge.extract_cited_claims(report)
    check("URL forms deduplicate in first-seen order", list(extracted) == [mixed])
    check("distinct claims retained and duplicate claim removed",
          len(extracted[mixed]) == 3 and len(set(extracted[mixed])) == 3,
          extracted[mixed])

    extracted = judge.extract_cited_claims(
        "Pay is listed on the page (https://example.com/a).")
    check("trailing punctuation excluded from URL",
          list(extracted) == ["https://example.com/a"], extracted)

    claim_url = "https://example.com/claim"
    uncited_url = "https://example.com/link-only"
    report = ("The employer lists a range from $90k to $110k in this role: "
              f"[Careers]({claim_url})\n[Employer career page]({uncited_url})")

    def check_report():
        result = judge.check_report(report, ask=ask)
        by_url = {source["url"]: source for source in result["sources"]}
        check("check_report includes both source URLs", set(by_url) == {claim_url, uncited_url})
        check("link-only source is marked uncited",
              by_url[uncited_url].get("uncited") is True)
        check("uncited source has no claim rows", by_url[uncited_url]["claims"] == [])
        check("uncited source contributes no checked claims", result["checked"] == 1)

    with_pages({claim_url: "Career information.", uncited_url: "Employer careers."},
               check_report)

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)

        report_file = root / "report.md"
        report_file.write_text(
            f"The employer lists a range from $90k to $110k in this role: [Careers]({claim_url})",
            encoding="utf-8")

        def check_report_file():
            output = dispatch.run_source_check("No URLs in the final message.",
                                               files=[str(report_file)], ask=ask)
            check("report file named in scan header",
                  output.startswith("scanned: final message, report.md\n"), output)
            check("report file claim counted", "1 cited claims across 1 sources" in output,
                  output)
            check("report file URL found", "no cited URLs found" not in output, output)

        with_pages({claim_url: "Career information."}, check_report_file)

        notes = root / "notes.txt"
        notes.write_text(
            "Sources:\n1. [Careers](https://example.com/careers)\n"
            "2. https://example.com/about", encoding="utf-8")

        def check_notes():
            output = dispatch.run_source_check("No URLs in the final message.",
                                               files=[str(notes)], ask=ask)
            check("sources list URLs are screened without claims",
                  "  SCREENED (no claim to check) https://example.com/careers" in output
                  and "  SCREENED (no claim to check) https://example.com/about" in output,
                  output)

        with_pages({"https://example.com/careers": "Career information.",
                    "https://example.com/about": "About the employer."}, check_notes)

        html = root / "page.html"
        html.write_text("<html><body>\n<p>Sources</p>\nhttps://example.com/html\n</body></html>",
                        encoding="utf-8")
        csv = root / "data.csv"
        csv.write_text("source\nhttps://example.com/csv\n", encoding="utf-8")

        def check_all_report_types():
            fetched = []
            stub = judge.fetch_url

            def recording_fetch(url, timeout=20):
                fetched.append(url)
                return stub(url, timeout)

            judge.fetch_url = recording_fetch
            try:
                output = dispatch.run_source_check("No URLs in the final message.",
                                                   files=[str(html), str(csv)], ask=ask)
            finally:
                judge.fetch_url = stub
            check("HTML and CSV filenames are scanned",
                  "scanned: final message, page.html, data.csv" in output, output)
            check("HTML and CSV links are fetched",
                  sorted(fetched) == ["https://example.com/csv", "https://example.com/html"],
                  repr(fetched))
            check("HTML and CSV links are screened",
                  "  SCREENED (no claim to check) https://example.com/html" in output
                  and "  SCREENED (no claim to check) https://example.com/csv" in output,
                  output)

        with_pages({"https://example.com/html": "HTML page.",
                    "https://example.com/csv": "CSV source."}, check_all_report_types)

        empty = root / "empty.md"
        empty.write_text("Nothing to cite here.", encoding="utf-8")
        image = root / "chart.png"
        image.write_bytes(b"https://example.com/should-not-be-read")
        output = dispatch.run_source_check("No URLs in the final message.",
                                           files=[str(empty), str(image)], ask=ask)
        check("no URL output is exact and PNG is ignored",
              output == ("scanned: final message, empty.md\n"
                         "no cited URLs found in the final message or the 1 files the worker wrote"),
              output)

    print("PASS")


if __name__ == "__main__":
    main()
