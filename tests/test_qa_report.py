import csv

from scripts.contact_schema import FIELDNAMES
from scripts.qa_report import build_report, load_rows, has_critical_failures


def make_row(**overrides):
    row = {field: "" for field in FIELDNAMES}
    row.update(
        {
            "record_id": "sample",
            "company_name": "Sample",
            "email": "info@example.com",
            "source_url": "https://example.com/contact",
            "verification_status": "mhlw_verified",
            "confidence": "high",
            "classification": "recruitment_agency",
            "source_accessed_at": "2026-05-21T11:00:00+08:00",
        }
    )
    row.update(overrides)
    return row


def test_build_report_counts_rows_statuses_and_no_failures_for_expected_count(tmp_path):
    csv_path = tmp_path / "contacts.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerow(make_row())

    rows = load_rows(csv_path)
    report = build_report(rows, expected_count=1)

    assert "Total accepted rows: 1" in report
    assert "mhlw_verified: 1" in report
    assert "Critical validation failures: 0" in report
    assert not has_critical_failures(report)


def test_build_report_treats_count_mismatch_as_critical_failure():
    report = build_report([make_row()], expected_count=2)

    assert "Expected rows: 2" in report
    assert "Critical validation failures: 1" in report
    assert "row count mismatch" in report
    assert has_critical_failures(report)
