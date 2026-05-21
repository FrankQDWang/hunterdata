from scripts.mhlw_collect import collect_from_mhlw, parse_detail_page, parse_search_results


def test_parse_search_results_extracts_unique_detail_urls_and_counts():
    html = """
    <span id="ID_lbSearchCount">34066</span>
    <span id="ID_lbSearchCurrentPage">2</span>
    <a href="./GICB102030.do?screenId=GICB102030&amp;action=detail&amp;detkey_Detail=01-%E3%83%A6-300259%2C1+++++">detail</a>
    <a href="./GICB102030.do?screenId=GICB102030&amp;action=detail&amp;detkey_Detail=01-%E3%83%A6-300259%2C1+++++">detail</a>
    <a href="./GICB102030.do?screenId=GICB102030&amp;action=detail&amp;detkey_Detail=13-%E3%83%A6-305810%2C1+++++">detail</a>
    """

    page = parse_search_results(html, base_url="https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/")

    assert page.total_count == 34066
    assert page.current_page == 2
    assert len(page.detail_urls) == 2
    assert page.detail_urls[0].startswith("https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/GICB102030.do")


def test_parse_detail_page_extracts_official_contact_fields():
    html = """
    <html><body>
    許可・届出受理番号 13-ユ-305810
    許可届出受理年月日 平成25年02月01日
    事業主名称 <a href="https://w3hr.jp">株式会社ウィンスリー</a>
    事業所名称 <a href="https://w3hr.jp">株式会社ウィンスリー</a>
    事業所所在地 東京都港区六本木四丁目８番７号
    電話番号 080-1014-2856
    取扱職種の範囲等 取扱職種 全職種
    </body></html>
    """

    detail = parse_detail_page(
        html,
        source_url="https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/GICB102030.do?action=detail",
    )

    assert detail.license_number == "13-ユ-305810"
    assert detail.license_type == "有料職業紹介事業"
    assert detail.company_name == "株式会社ウィンスリー"
    assert detail.phone == "080-1014-2856"
    assert detail.company_url == "https://w3hr.jp"
    assert detail.city_or_prefecture == "東京都"


def test_collect_from_mhlw_keeps_fetching_until_deduped_target_is_met(tmp_path, monkeypatch):
    search_page_1 = """
    <input name="hfCond" value="cond">
    <span id="ID_lbSearchCount">3</span><span id="ID_lbSearchCurrentPage">1</span>
    <a href="GICB102030.do?action=detail&detkey_Detail=01-%E3%83%A6-000001%2C0+++++">detail</a>
    <a href="GICB102030.do?action=detail&detkey_Detail=01-%E3%83%A6-000002%2C0+++++">detail</a>
    """
    search_page_2 = """
    <input name="hfCond" value="cond">
    <span id="ID_lbSearchCount">3</span><span id="ID_lbSearchCurrentPage">2</span>
    <a href="GICB102030.do?action=detail&detkey_Detail=01-%E3%83%A6-000003%2C0+++++">detail</a>
    """
    detail_duplicate_a = _detail_html("01-ユ-000001", "Duplicate Co", "011-111-1111")
    detail_duplicate_b = _detail_html("01-ユ-000002", "Duplicate Co", "011-111-1111")
    detail_unique = _detail_html("01-ユ-000003", "Unique Co", "011-222-2222")

    def fake_fetch(url, *, data=None, timeout=30):
        if data and b"action=search" in data:
            return search_page_1
        if data and b"action=page" in data:
            return search_page_2
        if "000001" in url:
            return detail_duplicate_a
        if "000002" in url:
            return detail_duplicate_b
        if "000003" in url:
            return detail_unique
        raise AssertionError(url)

    monkeypatch.setattr("scripts.mhlw_collect.fetch_url", fake_fetch)

    output = collect_from_mhlw(
        target_count=2,
        raw_dir=tmp_path / "raw",
        output_dir=tmp_path / "processed",
        interim_path=tmp_path / "interim" / "candidates.jsonl",
        sleep_seconds=0,
    )

    assert output.exists()


def _detail_html(license_number, company_name, phone):
    return f"""
    許可・届出受理番号 {license_number}
    事業主名称 {company_name}
    事業所名称 {company_name}
    事業所所在地 北海道札幌市
    電話番号 {phone}
    取扱職種の範囲等 取扱職種 全職種
    """
