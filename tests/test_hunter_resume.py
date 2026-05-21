import csv

from scripts.enrich_contacts import ENRICHED_FIELDNAMES
from scripts.hunter_resume import (
    prepare_next_batch,
    upsert_master_and_export,
)


def test_prepare_next_batch_skips_master_records_and_writes_contact_csv(tmp_path):
    manifest = tmp_path / "manifest.csv"
    master = tmp_path / "master.csv"
    batch = tmp_path / "run" / "batch.csv"
    _write_manifest(
        manifest,
        [
            {"record_id": "done", "company_name": "Done Co", "phone": "011-000-0000"},
            {"record_id": "next", "company_name": "Next Co", "phone": "011-111-1111"},
            {"record_id": "later", "company_name": "Later Co", "phone": "011-222-2222"},
        ],
    )
    _write_enriched(master, [{"record_id": "done", "company_name": "Done Co", "phone": "011-000-0000"}])

    rows = prepare_next_batch(manifest_csv=manifest, master_csv=master, batch_csv=batch, limit=1)

    assert [row["record_id"] for row in rows] == ["next"]
    with batch.open(newline="", encoding="utf-8") as handle:
        written = list(csv.DictReader(handle))
    assert written[0]["company_name"] == "Next Co"
    assert written[0]["verification_status"] == "mhlw_verified"


def test_upsert_master_and_export_updates_existing_and_writes_root_csv(tmp_path):
    master = tmp_path / "data" / "processed" / "master.csv"
    root = tmp_path / "hunter_contacts.csv"
    batch = tmp_path / "batch_final.csv"
    _write_enriched(master, [{"record_id": "same", "company_name": "Old", "phone": "011-000-0000"}])
    _write_enriched(
        batch,
        [
            {
                "record_id": "same",
                "company_name": "New",
                "phone": "011-999-9999",
                "email": "info@example.co.jp",
            },
            {"record_id": "new", "company_name": "Brand New", "phone": "011-111-1111"},
        ],
    )

    rows = upsert_master_and_export(batch_csv=batch, master_csv=master, root_csv=root)

    assert [row["record_id"] for row in rows] == ["same", "new"]
    assert rows[0]["company_name"] == "New"
    assert rows[0]["email"] == "info@example.co.jp"
    assert root.read_text(encoding="utf-8-sig").startswith("记录ID,公司名称")


def _write_manifest(path, rows):
    fieldnames = [
        "record_id",
        "company_name",
        "office_name",
        "phone",
        "company_url",
        "license_number",
        "license_type",
        "city_or_prefecture",
        "address",
        "mhlw_source_url",
        "mhlw_raw_path",
        "page_number",
        "detail_index",
        "collected_at",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            base = {field: "" for field in fieldnames}
            base.update(
                {
                    "office_name": row["company_name"],
                    "company_url": "",
                    "license_number": f"01-ユ-{index:06d}",
                    "license_type": "有料職業紹介事業",
                    "city_or_prefecture": "北海道",
                    "address": "北海道札幌市",
                    "mhlw_source_url": f"https://example.test/detail/{row['record_id']}",
                    "mhlw_raw_path": f"data/raw/mhlw/{row['record_id']}.html",
                    "page_number": "1",
                    "detail_index": str(index),
                    "collected_at": "2026-05-21T00:00:00+09:00",
                }
            )
            base.update(row)
            writer.writerow(base)


def _write_enriched(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ENRICHED_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            base = {field: "" for field in ENRICHED_FIELDNAMES}
            base.update(
                {
                    "source_url": "https://example.test/source",
                    "mhlw_source_url": "https://example.test/source",
                    "license_number": "01-ユ-000001",
                    "license_type": "有料職業紹介事業",
                    "city_or_prefecture": "北海道",
                    "classification": "recruitment_agency",
                    "evidence_keywords": "有料職業紹介事業",
                    "verification_status": "mhlw_verified",
                    "confidence": "high",
                    "source_accessed_at": "2026-05-21T00:00:00+09:00",
                    "enrichment_status": "not_found",
                }
            )
            base.update(row)
            writer.writerow(base)
