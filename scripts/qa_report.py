from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

from scripts.contact_schema import FIELDNAMES, ValidationError, dedupe_key, validate_row


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_report(rows: list[dict[str, str]], expected_count: int = 100) -> str:
    failures = []
    if len(rows) != expected_count:
        failures.append(f"row count mismatch: expected {expected_count}, got {len(rows)}")

    for index, row in enumerate(rows, start=2):
        try:
            validate_row(row)
        except ValidationError as exc:
            failures.append(f"CSV line {index}: {exc}")

    status_counts = Counter(row.get("verification_status", "") for row in rows)
    confidence_counts = Counter(row.get("confidence", "") for row in rows)
    classification_counts = Counter(row.get("classification", "") for row in rows)
    missing_counts = {
        field: sum(1 for row in rows if not row.get(field, "").strip())
        for field in FIELDNAMES
    }

    lines = [
        "# QA Report",
        "",
        f"Total accepted rows: {len(rows)}",
        f"Expected rows: {expected_count}",
        f"Critical validation failures: {len(failures)}",
        "",
        "## Verification Status",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(status_counts.items()))
    lines.extend(["", "## Confidence"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(confidence_counts.items()))
    lines.extend(["", "## Classification"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(classification_counts.items()))
    lines.extend(["", "## Missing Fields"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(missing_counts.items()))
    lines.extend(["", "## Failures"])
    if failures:
        lines.extend(f"- {failure}" for failure in failures)
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def build_master_report(rows: list[dict[str, str]]) -> str:
    failures = []
    for index, row in enumerate(rows, start=2):
        try:
            validate_row(row)
        except ValidationError as exc:
            failures.append(f"CSV line {index}: {exc}")

    status_counts = Counter(row.get("verification_status", "") for row in rows)
    confidence_counts = Counter(row.get("confidence", "") for row in rows)
    classification_counts = Counter(row.get("classification", "") for row in rows)
    likelihood_counts = Counter(row.get("hunter_likelihood", "") for row in rows)
    duplicate_record_ids = _duplicate_values(row.get("record_id", "") for row in rows)
    duplicate_contact_keys = _duplicate_contact_keys(rows)

    lines = [
        "# Master QA Report",
        "",
        f"Total rows: {len(rows)}",
        f"Critical validation failures: {len(failures)}",
        "",
        "## Verification Status",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(status_counts.items()))
    lines.extend(["", "## Confidence"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(confidence_counts.items()))
    lines.extend(["", "## Classification"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(classification_counts.items()))
    lines.extend(["", "## Hunter Likelihood"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(likelihood_counts.items()))
    lines.extend(["", "## Duplicate Record IDs"])
    if duplicate_record_ids:
        lines.extend(f"- {record_id}: {count}" for record_id, count in duplicate_record_ids)
    else:
        lines.append("- None")
    lines.extend(["", "## Potential Duplicate Contact Keys"])
    if duplicate_contact_keys:
        lines.extend(f"- {key}: {record_ids}" for key, record_ids in duplicate_contact_keys)
    else:
        lines.append("- None")
    lines.extend(["", "## Failures"])
    if failures:
        lines.extend(f"- {failure}" for failure in failures)
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _duplicate_values(values) -> list[tuple[str, int]]:
    counts = Counter(value for value in values if value)
    return sorted((value, count) for value, count in counts.items() if count > 1)


def _duplicate_contact_keys(rows: list[dict[str, str]]) -> list[tuple[str, str]]:
    grouped: dict[tuple[str, str, str, str], list[str]] = {}
    for row in rows:
        key = dedupe_key(row)
        grouped.setdefault(key, []).append(row.get("record_id", ""))
    duplicates = []
    for key, record_ids in grouped.items():
        clean_ids = [record_id for record_id in record_ids if record_id]
        if len(clean_ids) <= 1:
            continue
        duplicates.append((_format_duplicate_key(key), ", ".join(clean_ids)))
    return sorted(duplicates)


def _format_duplicate_key(key: tuple[str, str, str, str]) -> str:
    return "|".join(part for part in key if part)


def has_critical_failures(report: str) -> bool:
    match = re.search(r"Critical validation failures:\s*(\d+)", report)
    return bool(match and int(match.group(1)) > 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("data/processed/qa_report.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(load_rows(args.csv_path), expected_count=args.expected_count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output)
    if has_critical_failures(report):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
