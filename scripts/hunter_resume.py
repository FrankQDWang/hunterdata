from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Sequence

from scripts.contact_schema import FIELDNAMES
from scripts.enrich_contacts import ENRICHED_FIELDNAMES, write_chinese_csv, write_enriched_csv
from scripts.mhlw_manifest import append_next_mhlw_records


def prepare_next_batch(
    *,
    manifest_csv: Path = Path("data/manifest/mhlw_manifest.csv"),
    manifest_jsonl: Path = Path("data/manifest/mhlw_manifest.jsonl"),
    manifest_checkpoint: Path = Path("data/manifest/checkpoint.json"),
    mhlw_raw_dir: Path = Path("data/raw/mhlw"),
    master_csv: Path = Path("data/processed/master.csv"),
    batch_csv: Path,
    limit: int = 100,
    ensure_mhlw: bool = False,
    mhlw_sleep_seconds: float = 0.1,
) -> list[dict[str, str]]:
    if ensure_mhlw:
        ensure_manifest_capacity(
            manifest_csv=manifest_csv,
            manifest_jsonl=manifest_jsonl,
            manifest_checkpoint=manifest_checkpoint,
            mhlw_raw_dir=mhlw_raw_dir,
            master_csv=master_csv,
            limit=limit,
            sleep_seconds=mhlw_sleep_seconds,
        )
    manifest_rows = _load_csv(manifest_csv)
    completed_ids = _completed_record_ids(master_csv)
    selected = [row for row in manifest_rows if row.get("record_id", "") not in completed_ids][:limit]
    contact_rows = [manifest_to_contact_row(row) for row in selected]
    _write_contact_csv(contact_rows, batch_csv)
    return contact_rows


def ensure_manifest_capacity(
    *,
    manifest_csv: Path = Path("data/manifest/mhlw_manifest.csv"),
    manifest_jsonl: Path = Path("data/manifest/mhlw_manifest.jsonl"),
    manifest_checkpoint: Path = Path("data/manifest/checkpoint.json"),
    mhlw_raw_dir: Path = Path("data/raw/mhlw"),
    master_csv: Path = Path("data/processed/master.csv"),
    limit: int = 100,
    sleep_seconds: float = 0.1,
) -> int:
    while True:
        unprocessed = _unprocessed_manifest_rows(manifest_csv, master_csv)
        if len(unprocessed) >= limit:
            return len(unprocessed)
        needed = limit - len(unprocessed)
        before = len(_load_csv(manifest_csv))
        result = append_next_mhlw_records(
            manifest_csv=manifest_csv,
            manifest_jsonl=manifest_jsonl,
            checkpoint_path=manifest_checkpoint,
            raw_dir=mhlw_raw_dir,
            limit=needed,
            sleep_seconds=sleep_seconds,
        )
        after = len(_load_csv(manifest_csv))
        if result.appended_records == 0 or after <= before:
            return len(_unprocessed_manifest_rows(manifest_csv, master_csv))


def manifest_to_contact_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "record_id": row.get("record_id", ""),
        "company_name": row.get("company_name", ""),
        "contact_name": "",
        "title": "",
        "email": "",
        "phone": row.get("phone", ""),
        "contact_form_url": "",
        "company_url": row.get("company_url", ""),
        "source_url": row.get("mhlw_source_url", ""),
        "mhlw_source_url": row.get("mhlw_source_url", ""),
        "license_number": row.get("license_number", ""),
        "license_type": row.get("license_type", ""),
        "city_or_prefecture": row.get("city_or_prefecture", ""),
        "specialization": "全行业",
        "classification": "recruitment_agency",
        "hunter_likelihood": "low",
        "hunter_likelihood_reason": "MHLW only proves a paid occupational placement license; headhunter positioning is not confirmed yet.",
        "evidence_keywords": "有料職業紹介事業; 厚生労働省",
        "verification_status": "mhlw_verified",
        "confidence": "high",
        "source_accessed_at": row.get("collected_at", ""),
        "notes": "Contact phone sourced from MHLW public occupational placement business detail page.",
    }


def upsert_master_and_export(
    *,
    batch_csv: Path,
    master_csv: Path = Path("data/processed/master.csv"),
    root_csv: Path = Path("hunter_contacts.csv"),
    master_zh_csv: Path = Path("data/processed/master_zh.csv"),
) -> list[dict[str, str]]:
    existing = [normalize_enriched_row(row) for row in _load_csv(master_csv)] if master_csv.exists() else []
    incoming = [normalize_enriched_row(row) for row in _load_csv(batch_csv)]
    order = [row["record_id"] for row in existing]
    by_id = {row["record_id"]: row for row in existing}
    for row in incoming:
        record_id = row["record_id"]
        if record_id not in by_id:
            order.append(record_id)
        by_id[record_id] = row
    merged = [by_id[record_id] for record_id in order]
    write_enriched_csv(merged, master_csv)
    write_chinese_csv(merged, master_zh_csv)
    write_chinese_csv(merged, root_csv)
    return merged


def normalize_enriched_row(row: dict[str, str]) -> dict[str, str]:
    return {field: row.get(field, "") for field in ENRICHED_FIELDNAMES}


def _unprocessed_manifest_rows(manifest_csv: Path, master_csv: Path) -> list[dict[str, str]]:
    completed_ids = _completed_record_ids(master_csv)
    return [row for row in _load_csv(manifest_csv) if row.get("record_id", "") not in completed_ids]


def _completed_record_ids(master_csv: Path) -> set[str]:
    if not master_csv.exists():
        return set()
    return {row.get("record_id", "") for row in _load_csv(master_csv) if row.get("record_id", "")}


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _write_contact_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare resumable hunter contact batches and upsert master outputs.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-next-batch")
    prepare.add_argument("--manifest-csv", type=Path, default=Path("data/manifest/mhlw_manifest.csv"))
    prepare.add_argument("--manifest-jsonl", type=Path, default=Path("data/manifest/mhlw_manifest.jsonl"))
    prepare.add_argument("--manifest-checkpoint", type=Path, default=Path("data/manifest/checkpoint.json"))
    prepare.add_argument("--mhlw-raw-dir", type=Path, default=Path("data/raw/mhlw"))
    prepare.add_argument("--master-csv", type=Path, default=Path("data/processed/master.csv"))
    prepare.add_argument("--batch-csv", type=Path, required=True)
    prepare.add_argument("--limit", type=int, default=100)
    prepare.add_argument("--ensure-mhlw", action="store_true")
    prepare.add_argument("--mhlw-sleep-seconds", type=float, default=0.1)

    upsert = subparsers.add_parser("upsert-master")
    upsert.add_argument("--batch-csv", type=Path, required=True)
    upsert.add_argument("--master-csv", type=Path, default=Path("data/processed/master.csv"))
    upsert.add_argument("--master-zh-csv", type=Path, default=Path("data/processed/master_zh.csv"))
    upsert.add_argument("--root-csv", type=Path, default=Path("hunter_contacts.csv"))
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    if args.command == "prepare-next-batch":
        rows = prepare_next_batch(
            manifest_csv=args.manifest_csv,
            manifest_jsonl=args.manifest_jsonl,
            manifest_checkpoint=args.manifest_checkpoint,
            mhlw_raw_dir=args.mhlw_raw_dir,
            master_csv=args.master_csv,
            batch_csv=args.batch_csv,
            limit=args.limit,
            ensure_mhlw=args.ensure_mhlw,
            mhlw_sleep_seconds=args.mhlw_sleep_seconds,
        )
        print(args.batch_csv)
        print(f"rows={len(rows)}")
        return
    if args.command == "upsert-master":
        rows = upsert_master_and_export(
            batch_csv=args.batch_csv,
            master_csv=args.master_csv,
            root_csv=args.root_csv,
            master_zh_csv=args.master_zh_csv,
        )
        print(args.master_csv)
        print(args.root_csv)
        print(f"rows={len(rows)}")


if __name__ == "__main__":
    main()
