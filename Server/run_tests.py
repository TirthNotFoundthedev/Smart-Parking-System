import argparse
import html
import sys
import traceback
import unittest
from datetime import datetime
from pathlib import Path


class HtmlTestResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = []

    @staticmethod
    def _test_name(test):
        return test.id().split(".")[-1].removeprefix("test_").replace("_", " ")

    def getDescription(self, test):
        return self._test_name(test)

    def _record(self, test, outcome, details=""):
        self.outcomes.append(
            {"name": self._test_name(test), "outcome": outcome, "details": details}
        )

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "FAIL", "".join(traceback.format_exception(*err)))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "ERROR", "".join(traceback.format_exception(*err)))

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, "XFAIL", "".join(traceback.format_exception(*err)))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, "XPASS", "This test was expected to fail but passed.")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "SKIP", reason)


def write_report(report_path, result):
    counts = {
        outcome: sum(item["outcome"] == outcome for item in result.outcomes)
        for outcome in ("PASS", "FAIL", "ERROR", "XFAIL", "XPASS", "SKIP")
    }
    rows = []
    for item in result.outcomes:
        details = (
            f"<details><summary>Details</summary><pre>{html.escape(item['details'])}</pre></details>"
            if item["details"]
            else ""
        )
        rows.append(
            "<tr class='{outcome}'><td><span class='badge'>{outcome}</span></td>"
            "<td>{name}</td><td>{details}</td></tr>".format(
                outcome=item["outcome"],
                name=html.escape(item["name"]),
                details=details,
            )
        )

    summary_cards = "".join(
        "<div class='card {css_class}'><strong>{value}</strong><span>{label}</span></div>".format(
            css_class=key.lower(),
            value=value,
            label=key,
        )
        for key, value in counts.items()
        if value
    )
    status = (
        "NO TESTS FOUND"
        if result.testsRun == 0
        else "PASSED"
        if result.wasSuccessful()
        else "FAILED"
    )
    document = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Server test results - {status}</title>
  <style>
    :root {{ color-scheme: light dark; font: 15px/1.5 system-ui, sans-serif; }}
    body {{ max-width: 1050px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ margin-bottom: .25rem; }}
    .muted {{ color: #777; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: .7rem; margin: 1.5rem 0; }}
    .card {{ min-width: 90px; padding: .7rem 1rem; border-radius: 8px; background: #eee; color: #222; }}
    .card strong, .card span {{ display: block; }}
    .card strong {{ font-size: 1.5rem; }}
    .card.pass {{ background: #dcfce7; }} .card.fail, .card.error, .card.xpass {{ background: #fee2e2; }}
    .card.xfail {{ background: #fef3c7; }} .card.skip {{ background: #e0e7ff; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; vertical-align: top; padding: .65rem; border-bottom: 1px solid #bbb; }}
    th {{ position: sticky; top: 0; background: Canvas; }}
    .badge {{ font-weight: 700; }}
    tr.FAIL .badge, tr.ERROR .badge, tr.XPASS .badge {{ color: #c00; }}
    tr.XFAIL .badge {{ color: #9a6700; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; max-height: 28rem; overflow: auto; }}
    @media (prefers-color-scheme: dark) {{
      .card {{ background: #333; color: #eee; }} .card.pass {{ background: #164b2c; }}
      .card.fail, .card.error, .card.xpass {{ background: #5a2020; }} .card.xfail {{ background: #594611; }}
      .card.skip {{ background: #292e50; }}
    }}
  </style>
</head>
<body>
  <h1>Test results: {status}</h1>
  <p class="muted">Generated {generated} · {total} test(s)</p>
  <section class="summary">{summary}</section>
  <table>
    <thead><tr><th>Result</th><th>Test case</th><th>Failure / note</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
""".format(
        status=status,
        generated=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        total=len(result.outcomes),
        summary=summary_cards,
        rows="".join(rows),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(document, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Run all server tests and write an HTML report.")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(__file__).resolve().parent / "result.html",
        help="HTML report output path (default: Server/result.html)",
    )
    args = parser.parse_args()

    server_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(server_dir / "src"))
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(server_dir / "tests"),
        pattern="test*.py",
    )
    runner = unittest.TextTestRunner(
        stream=sys.stdout,
        verbosity=2,
        resultclass=HtmlTestResult,
    )
    result = runner.run(suite)
    report_path = args.report.resolve()
    write_report(report_path, result)
    print(f"\nHTML report: {report_path}")
    print(
        "Summary: "
        + ", ".join(
            f"{outcome} {sum(item['outcome'] == outcome for item in result.outcomes)}"
            for outcome in ("PASS", "FAIL", "ERROR", "XFAIL", "XPASS", "SKIP")
            if any(item["outcome"] == outcome for item in result.outcomes)
        )
    )
    return 0 if result.wasSuccessful() and result.testsRun > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
