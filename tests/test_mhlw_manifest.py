import csv
import json

from scripts.mhlw_manifest import refresh_mhlw_manifest


def test_refresh_mhlw_manifest_writes_stable_manifest_and_checkpoint(tmp_path, monkeypatch):
    search_page = """
    <input name="hfCond" value="cond">
    <span id="ID_lbSearchCount">2</span><span id="ID_lbSearchCurrentPage">1</span>
    <a href="GICB102030.do?action=detail&detkey_Detail=01-%E3%83%A6-000001%2C0+++++">detail</a>
    <a href="GICB102030.do?action=detail&detkey_Detail=01-%E3%83%A6-000002%2C0+++++">detail</a>
    """
    detail_one = _detail_html("01-ユ-000001", "One Co", "011-111-1111")
    detail_two = _detail_html("01-ユ-000002", "Two Co", "011-222-2222")

    def fake_fetch(url, *, data=None, timeout=30):
        if data:
            return search_page
        if "000001" in url:
            return detail_one
        if "000002" in url:
            return detail_two
        raise AssertionError(url)

    monkeypatch.setattr("scripts.mhlw_manifest.fetch_url", fake_fetch)

    result = refresh_mhlw_manifest(
        manifest_csv=tmp_path / "manifest" / "mhlw_manifest.csv",
        manifest_jsonl=tmp_path / "manifest" / "mhlw_manifest.jsonl",
        checkpoint_path=tmp_path / "manifest" / "checkpoint.json",
        raw_dir=tmp_path / "raw",
        limit=2,
        sleep_seconds=0,
    )

    with result.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    jsonl_rows = [json.loads(line) for line in result.jsonl_path.read_text(encoding="utf-8").splitlines()]
    checkpoint = json.loads(result.checkpoint_path.read_text(encoding="utf-8"))

    assert [row["company_name"] for row in rows] == ["One Co", "Two Co"]
    assert rows[0]["record_id"] == jsonl_rows[0]["record_id"]
    assert rows[0]["phone"] == "011-111-1111"
    assert rows[0]["mhlw_source_url"].startswith("https://jinzai.hellowork.mhlw.go.jp/")
    assert checkpoint["done"] is True
    assert checkpoint["records"] == 2


def _detail_html(license_number, company_name, phone):
    return f"""
    許可・届出受理番号 {license_number}
    事業主名称 {company_name}
    事業所名称 {company_name}
    事業所所在地 北海道札幌市
    電話番号 {phone}
    取扱職種の範囲等 取扱職種 全職種
    """
