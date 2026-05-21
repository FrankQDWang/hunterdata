from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


FIELDNAMES = [
    "record_id",
    "company_name",
    "contact_name",
    "title",
    "email",
    "phone",
    "contact_form_url",
    "company_url",
    "source_url",
    "mhlw_source_url",
    "license_number",
    "license_type",
    "city_or_prefecture",
    "specialization",
    "classification",
    "evidence_keywords",
    "verification_status",
    "confidence",
    "source_accessed_at",
    "notes",
]

VALID_VERIFICATION_STATUSES = {
    "mhlw_verified",
    "association_verified",
    "business_keyword_verified",
    "needs_manual_review",
}
VALID_CONFIDENCES = {"high", "medium", "low"}
VALID_CLASSIFICATIONS = {
    "executive_search",
    "recruitment_agency",
    "staffing_or_dispatch",
    "mixed_hr_service",
    "unknown",
}


class ValidationError(ValueError):
    """Raised when a processed contact row violates the dataset contract."""


@dataclass(frozen=True)
class ContactRow:
    data: dict[str, str]
    source_text_path: str = ""
    mhlw_text_path: str = ""
    association_text_path: str = ""

    def as_dict(self) -> dict[str, str]:
        row = {field: str(self.data.get(field, "") or "") for field in FIELDNAMES}
        validate_row(row)
        return row


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def normalize_key(value: str) -> str:
    return normalize_whitespace(value).casefold()


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def stable_record_id(company_name: str, source_url: str) -> str:
    base = normalize_key(company_name)
    slug_parts = re.findall(r"[a-z0-9]+", base)
    slug = "-".join(slug_parts) or "company"
    digest = hashlib.sha1(f"{base}|{source_url.strip()}".encode("utf-8")).hexdigest()[:8]
    return f"{slug[:48].strip('-')}-{digest}"


def dedupe_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        normalize_key(row.get("company_name", "")),
        normalize_key(row.get("email", "")),
        normalize_whitespace(row.get("phone", "")),
        normalize_key(row.get("contact_form_url", "")),
    )


def validate_row(row: dict[str, str]) -> None:
    missing_fields = [field for field in FIELDNAMES if field not in row]
    if missing_fields:
        raise ValidationError(f"missing fields: {', '.join(missing_fields)}")

    if not row["record_id"].strip():
        raise ValidationError("record_id is required")
    if not row["company_name"].strip():
        raise ValidationError("company_name is required")
    if not row["source_url"].strip():
        raise ValidationError("source_url is required")
    if not _is_http_url(row["source_url"].strip()):
        raise ValidationError("source_url must be an absolute http(s) URL")
    if row["contact_form_url"].strip() and not _is_http_url(row["contact_form_url"].strip()):
        raise ValidationError("contact_form_url must be an absolute http(s) URL")
    if not any(row[field].strip() for field in ("email", "phone", "contact_form_url")):
        raise ValidationError("at least one contact channel is required")
    if row["verification_status"] not in VALID_VERIFICATION_STATUSES:
        raise ValidationError("verification_status is invalid or empty")
    if row["confidence"] not in VALID_CONFIDENCES:
        raise ValidationError("confidence is invalid or empty")
    if row["classification"] not in VALID_CLASSIFICATIONS:
        raise ValidationError("classification is invalid or empty")
    if not row["source_accessed_at"].strip():
        raise ValidationError("source_accessed_at is required")


def write_outputs(rows: list[ContactRow], csv_path: Path, jsonl_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_rows = [row.as_dict() for row in rows]

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(normalized_rows)

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in normalized_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")
