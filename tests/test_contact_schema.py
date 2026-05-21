import csv
import json

import pytest

from scripts.contact_schema import (
    FIELDNAMES,
    ContactRow,
    ValidationError,
    dedupe_key,
    stable_record_id,
    validate_row,
    write_outputs,
)


def make_row(**overrides):
    data = {field: "" for field in FIELDNAMES}
    data.update(
        {
            "record_id": "sample-recruiting-12345678",
            "company_name": "Sample Recruiting",
            "email": "info@example.com",
            "company_url": "https://example.com",
            "source_url": "https://example.com/contact",
            "classification": "recruitment_agency",
            "evidence_keywords": "人材紹介",
            "verification_status": "mhlw_verified",
            "confidence": "high",
            "source_accessed_at": "2026-05-21T11:00:00+08:00",
        }
    )
    data.update(overrides)
    return data


def test_validate_row_accepts_valid_company_contact():
    validate_row(make_row())


def test_validate_row_rejects_missing_source_url():
    with pytest.raises(ValidationError, match="source_url"):
        validate_row(make_row(source_url=""))


def test_validate_row_rejects_missing_contact_channel():
    with pytest.raises(ValidationError, match="contact channel"):
        validate_row(make_row(email="", phone="", contact_form_url=""))


def test_write_outputs_preserves_field_order_and_jsonl(tmp_path):
    row = ContactRow(make_row())
    csv_path = tmp_path / "contacts.csv"
    jsonl_path = tmp_path / "contacts.jsonl"

    write_outputs([row], csv_path, jsonl_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == FIELDNAMES
        assert list(reader)[0]["company_name"] == "Sample Recruiting"

    json_rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert json_rows[0]["record_id"] == "sample-recruiting-12345678"


def test_stable_record_id_and_dedupe_key_are_normalized():
    first = stable_record_id(" Sample Recruiting K.K. ", "https://example.com/contact?a=1")
    second = stable_record_id("Sample Recruiting K.K.", "https://example.com/contact?a=1")

    assert first == second
    assert dedupe_key(make_row(company_name=" Sample Recruiting ", email="INFO@EXAMPLE.COM")) == (
        "sample recruiting",
        "info@example.com",
        "",
        "",
    )
