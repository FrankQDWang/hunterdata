import csv
import json

import pytest

from scripts.collect_contacts import QualityGateError, load_candidate_records, write_accepted_records


def write_raw(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def make_candidate(tmp_path, company_name, source_text, mhlw_text):
    source_path = write_raw(tmp_path / f"{company_name}-source.txt", source_text)
    mhlw_path = write_raw(tmp_path / f"{company_name}-mhlw.txt", mhlw_text)
    return {
        "company_name": company_name,
        "company_url": "https://example.com",
        "source_url": f"https://example.com/{company_name}/contact",
        "source_text_path": source_path,
        "mhlw_text_path": mhlw_path,
        "mhlw_source_url": "https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/",
        "association_text_path": "",
        "accessed_at": "2026-05-21T11:00:00+08:00",
    }


def test_load_candidate_records_extracts_verified_contacts(tmp_path):
    candidate = make_candidate(
        tmp_path,
        "sample",
        "Sample Recruiting TEL: 03-1234-5678 お問い合わせ /contact 人材紹介",
        "有料職業紹介事業 許可番号 13-ユ-010101",
    )
    manifest = tmp_path / "candidates.jsonl"
    manifest.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")

    records = load_candidate_records(manifest)

    assert records[0].data["company_name"] == "sample"
    assert records[0].data["phone"] == "03-1234-5678"
    assert records[0].data["verification_status"] == "mhlw_verified"
    assert records[0].data["license_number"] == "13-ユ-010101"


def test_load_candidate_records_classifies_mhlw_detail_as_recruitment_despite_site_navigation(tmp_path):
    candidate = make_candidate(
        tmp_path,
        "sample",
        "労働者派遣事業検索 職業紹介事業詳細 TEL: 03-1234-5678 許可・届出受理番号 13-ユ-010101",
        "労働者派遣事業検索 職業紹介事業詳細 許可・届出受理番号 13-ユ-010101",
    )
    candidate["source_url"] = "https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/GICB102030.do?action=detail"
    candidate["mhlw_source_url"] = candidate["source_url"]
    manifest = tmp_path / "candidates.jsonl"
    manifest.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")

    records = load_candidate_records(manifest)

    assert records[0].data["classification"] == "recruitment_agency"
    assert records[0].data["evidence_keywords"] == "職業紹介"


def test_write_accepted_records_fails_when_target_count_not_met(tmp_path):
    candidate = make_candidate(
        tmp_path,
        "sample",
        "Sample Recruiting info@example.com 人材紹介",
        "有料職業紹介事業 許可番号 13-ユ-010101",
    )
    manifest = tmp_path / "candidates.jsonl"
    manifest.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
    records = load_candidate_records(manifest)

    with pytest.raises(QualityGateError, match="target count"):
        write_accepted_records(records, tmp_path / "processed", target_count=2)


def test_write_accepted_records_outputs_contacts_and_auditable_sources(tmp_path):
    candidates = [
        make_candidate(
            tmp_path,
            "sample-a",
            "Sample A TEL: 03-1234-5678 人材紹介",
            "有料職業紹介事業 許可番号 13-ユ-010101",
        ),
        make_candidate(
            tmp_path,
            "sample-b",
            "Sample B info@example.jp エグゼクティブサーチ",
            "有料職業紹介事業 許可番号 13-ユ-020202",
        ),
    ]
    manifest = tmp_path / "candidates.jsonl"
    manifest.write_text(
        "\n".join(json.dumps(candidate, ensure_ascii=False) for candidate in candidates) + "\n",
        encoding="utf-8",
    )

    outputs = write_accepted_records(load_candidate_records(manifest), tmp_path / "processed", target_count=2)

    with outputs.sources_path.open(newline="", encoding="utf-8") as handle:
        source_rows = list(csv.DictReader(handle))

    assert len(source_rows) == 2
    assert source_rows[0]["source_text_path"].endswith("sample-a-source.txt")
    assert source_rows[0]["mhlw_text_path"].endswith("sample-a-mhlw.txt")
