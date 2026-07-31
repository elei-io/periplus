"""Run the checked-in public-SQL query benchmark scenarios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from atlas.query.benchmarking import compare_reports, discover_cases, report_payload


DEFAULT_CASE_ROOT = Path(__file__).resolve().parents[2] / "benchmarks/query/cases"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-root", type=Path, default=DEFAULT_CASE_ROOT)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--scale", type=int, action="append", dest="scales")
    parser.add_argument("--warm-runs", type=int, default=2)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    arguments = parser.parse_args()
    if arguments.warm_runs < 1:
        parser.error("--warm-runs must be positive")

    discovered = discover_cases(arguments.case_root)
    requested = arguments.case_ids or list(discovered)
    missing = sorted(set(requested) - set(discovered))
    if missing:
        parser.error("unknown cases: " + ", ".join(missing))
    selected = []
    for identifier in requested:
        case = discovered[identifier]
        if arguments.scales:
            invalid = sorted(set(arguments.scales) - set(case.scales))
            if invalid:
                parser.error(
                    f"case {identifier} does not define scales: "
                    + ", ".join(str(item) for item in invalid)
                )
            case = case.__class__(
                identifier=case.identifier,
                title=case.title,
                use_case=case.use_case,
                classification=case.classification,
                ordered=case.ordered,
                scales=tuple(arguments.scales),
                memory_limit=case.memory_limit,
                max_warm_ms=case.max_warm_ms,
                sql=case.sql,
                directory=case.directory,
            )
        selected.append(case)

    payload = report_payload(selected, warm_runs=arguments.warm_runs)
    failures = [
        f"time budget exceeded for {item['case']} scale={item.get('scale')}"
        for item in payload["measurements"]
        if not item["within_time_budget"]
    ]
    if arguments.baseline:
        baseline = json.loads(arguments.baseline.read_text(encoding="utf-8"))
        comparisons, comparison_failures = compare_reports(baseline, payload)
        payload["comparison"] = comparisons
        failures.extend(comparison_failures)
    payload["failures"] = failures
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"report={arguments.report.resolve()}")
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
