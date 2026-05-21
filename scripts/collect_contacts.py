from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

from scripts.contact_schema import (
    ContactRow,
    dedupe_key,
    stable_record_id,
    validate_row,
    write_outputs,
)
from scripts.dokobot_client import DokobotClient
from scripts.extract_contacts import (
    classify_business,
    extract_contact_forms,
    extract_emails,
    extract_keywords,
    extract_phones,
)
from scripts.enrich_contacts import classify_hunter_likelihood
from scripts.qa_report import build_report, has_critical_failures
from scripts.verify_sources import verify_sources


class QualityGateError(RuntimeError):
    """Raised when final output would not satisfy acceptance criteria."""


@dataclass(frozen=True)
class OutputPaths:
    csv_path: Path
    jsonl_path: Path
    sources_path: Path
    qa_path: Path


SOURCE_FIELDNAMES = [
    "record_id",
    "company_name",
    "source_url",
    "source_text_path",
    "mhlw_source_url",
    "mhlw_text_path",
    "association_text_path",
    "verification_status",
]


def read_text_or_empty(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists():
        raise QualityGateError(f"raw source path does not exist: {path}")
    return path.read_text(encoding="utf-8")


def load_candidate_records(candidate_path: Path) -> list[ContactRow]:
    records = []
    with candidate_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            records.append(candidate_to_contact_row(item, line_number=line_number))
    return records


def candidate_to_contact_row(item: dict[str, str], *, line_number: int) -> ContactRow:
    source_text = read_text_or_empty(item.get("source_text_path", ""))
    mhlw_text = read_text_or_empty(item.get("mhlw_text_path", ""))
    association_text = read_text_or_empty(item.get("association_text_path", ""))
    source_url = item.get("source_url", "").strip()
    company_name = item.get("company_name", "").strip()

    emails = extract_emails(source_text)
    phones = extract_phones(source_text)
    contact_forms = extract_contact_forms(source_text, base_url=item.get("company_url") or source_url)
    verification = verify_sources(
        mhlw_text=mhlw_text,
        association_text=association_text,
        business_text=source_text,
    )
    classification = classify_business([source_text, mhlw_text, association_text])
    hunter = classify_hunter_likelihood("\n".join([source_text, mhlw_text, association_text]))
    keywords = extract_keywords("\n".join([source_text, mhlw_text, association_text]))
    if _is_mhlw_occupational_placement_page(source_url, verification.license_type):
        classification = "recruitment_agency"
        keywords = ["職業紹介"]

    row = {
        "record_id": stable_record_id(company_name, source_url),
        "company_name": company_name,
        "contact_name": item.get("contact_name", "").strip(),
        "title": item.get("title", "").strip(),
        "email": emails[0] if emails else "",
        "phone": phones[0] if phones else "",
        "contact_form_url": contact_forms[0] if contact_forms else "",
        "company_url": item.get("company_url", "").strip(),
        "source_url": source_url,
        "mhlw_source_url": item.get("mhlw_source_url", "").strip(),
        "license_number": verification.license_number,
        "license_type": verification.license_type,
        "city_or_prefecture": item.get("city_or_prefecture", "").strip(),
        "specialization": item.get("specialization", "").strip(),
        "classification": classification,
        "hunter_likelihood": hunter.likelihood,
        "hunter_likelihood_reason": hunter.reason,
        "evidence_keywords": ";".join(keywords),
        "verification_status": verification.verification_status,
        "confidence": verification.confidence,
        "source_accessed_at": item.get("accessed_at", "").strip(),
        "notes": item.get("notes", "").strip(),
    }

    try:
        validate_row(row)
    except Exception as exc:
        raise QualityGateError(f"candidate line {line_number} is invalid: {exc}") from exc

    return ContactRow(
        row,
        source_text_path=item.get("source_text_path", ""),
        mhlw_text_path=item.get("mhlw_text_path", ""),
        association_text_path=item.get("association_text_path", ""),
    )


def _is_mhlw_occupational_placement_page(source_url: str, license_type: str) -> bool:
    return "jinzai.hellowork.mhlw.go.jp" in source_url and "職業紹介事業" in license_type


def write_sources_csv(rows: list[ContactRow], sources_path: Path) -> None:
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    with sources_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_FIELDNAMES)
        writer.writeheader()
        for record in rows:
            row = record.as_dict()
            writer.writerow(
                {
                    "record_id": row["record_id"],
                    "company_name": row["company_name"],
                    "source_url": row["source_url"],
                    "source_text_path": record.source_text_path,
                    "mhlw_source_url": row["mhlw_source_url"],
                    "mhlw_text_path": record.mhlw_text_path,
                    "association_text_path": record.association_text_path,
                    "verification_status": row["verification_status"],
                }
            )


def _accepted_records(records: list[ContactRow], target_count: int) -> list[ContactRow]:
    accepted = []
    seen = set()
    for record in records:
        row = record.as_dict()
        if row["verification_status"] == "needs_manual_review":
            continue
        if row["confidence"] == "low":
            continue
        if row["classification"] == "unknown":
            continue
        key = dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        accepted.append(record)
        if len(accepted) == target_count:
            break
    if len(accepted) != target_count:
        raise QualityGateError(f"accepted row count {len(accepted)} did not match target count {target_count}")
    return accepted


def write_accepted_records(records: list[ContactRow], output_dir: Path, target_count: int) -> OutputPaths:
    accepted = _accepted_records(records, target_count)

    csv_path = output_dir / "japan_headhunters_contacts.csv"
    jsonl_path = output_dir / "japan_headhunters_contacts.jsonl"
    sources_path = output_dir / "japan_headhunters_sources.csv"
    qa_path = output_dir / "qa_report.md"
    write_outputs(accepted, csv_path, jsonl_path)
    write_sources_csv(accepted, sources_path)

    report = build_report([record.as_dict() for record in accepted], expected_count=target_count)
    qa_path.write_text(report, encoding="utf-8")
    if has_critical_failures(report):
        raise QualityGateError("QA report contains critical validation failures")
    return OutputPaths(csv_path=csv_path, jsonl_path=jsonl_path, sources_path=sources_path, qa_path=qa_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-count", type=int, default=100)
    parser.add_argument("--use-dokobot-local", action="store_true")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/dokobot"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--candidate-path", type=Path, default=Path("data/interim/candidates.jsonl"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = DokobotClient(raw_dir=args.raw_dir, local=args.use_dokobot_local)
    if not args.candidate_path.exists():
        print("Dokobot client ready:", client.raw_dir)
        print("Create data/interim/candidates.jsonl from captured official and company source reads.")
        raise SystemExit(2)
    outputs = write_accepted_records(
        load_candidate_records(args.candidate_path),
        args.output_dir,
        target_count=args.target_count,
    )
    print("Wrote:", ", ".join(str(path) for path in outputs.__dict__.values()))


if __name__ == "__main__":
    main()
