import csv

from scripts.enrich_contacts import (
    CHINESE_HEADERS,
    EnrichmentResult,
    choose_official_result,
    contact_links,
    enrich_row_from_pages,
    is_relevant_page,
    parse_bing_rss,
    parse_duckduckgo_html,
    search_queries,
    write_chinese_csv,
)


def test_parse_bing_rss_extracts_result_items():
    rss = """
    <rss><channel>
      <item><title>Official</title><link>https://example.com/</link><description>desc</description></item>
      <item><title>Directory</title><link>https://mapion.co.jp/example</link><description>desc</description></item>
    </channel></rss>
    """

    results = parse_bing_rss(rss)

    assert results[0].title == "Official"
    assert results[0].url == "https://example.com/"


def test_parse_duckduckgo_html_decodes_result_redirects():
    html = """
    <html><body>
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdoshin-access.jp%2F&amp;rut=abc">
        株式会社道新アクセス
      </a>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F">
        ignored non-title link
      </a>
    </body></html>
    """

    results = parse_duckduckgo_html(html)

    assert len(results) == 1
    assert results[0].title == "株式会社道新アクセス"
    assert results[0].url == "https://doshin-access.jp/"


def test_choose_official_result_prefers_matching_site_over_directories():
    results = parse_bing_rss(
        """
        <rss><channel>
          <item><title>株式会社サンプル - マピオン</title><link>https://www.mapion.co.jp/sample</link><description>directory</description></item>
          <item><title>株式会社サンプル 公式サイト</title><link>https://sample.co.jp/</link><description>株式会社サンプル</description></item>
        </channel></rss>
        """
    )

    chosen = choose_official_result(results, company_name="株式会社 サンプル", phone="03-1234-5678")

    assert chosen is not None
    assert chosen.url == "https://sample.co.jp/"


def test_choose_official_result_prefers_company_title_over_directory_phone_match():
    results = parse_bing_rss(
        """
        <rss><channel>
          <item><title>SAPPORO SYOUEI 厚生労働大臣許可 株式会社 札幌昭栄</title><link>http://www.sapporo-syouei.co.jp/</link><description>official</description></item>
          <item><title>配膳会まとめ</title><link>https://www.haizenstaff.net/hokkaidou.html</link><description>株式会社 札幌昭栄 011-233-1811 http://www.sapporo-syouei.co.jp/</description></item>
        </channel></rss>
        """
    )

    chosen = choose_official_result(results, company_name="株式会社 札幌昭栄", phone="011-233-1811")

    assert chosen is not None
    assert chosen.url == "http://www.sapporo-syouei.co.jp/"


def test_choose_official_result_rejects_third_party_directory_hosts():
    search_results = parse_bing_rss(
        """
        <rss><channel>
          <item><title>株式会社 札総</title><link>https://zinzai-haken.com/example</link><description>011-1234-5678</description></item>
          <item><title>法人番号検索</title><link>https://houjin.goo.to/corporations/123/contact</link><description>株式会社 札総</description></item>
          <item><title>電話番号検索</title><link>https://www.jpnumber.com/numberinfo_011_123_4567.html</link><description>株式会社 札総</description></item>
          <item><title>地図検索</title><link>https://map.yahoo.co.jp/v3/place/example</link><description>株式会社 札総</description></item>
          <item><title>会社情報</title><link>https://kaisharesearch.com/company/detail/123/</link><description>株式会社 札総</description></item>
          <item><title>法人情報</title><link>https://www.houjin.info/detail/123/</link><description>株式会社 札総</description></item>
          <item><title>法人情報</title><link>https://presspage.biz/corporation/123/</link><description>株式会社 札総</description></item>
          <item><title>地図</title><link>https://www.navitime.co.jp/poi?spot=00011-010128831</link><description>株式会社 札総</description></item>
          <item><title>協会検索</title><link>https://www.jassa.or.jp/search/list/page/?companyId=1080</link><description>株式会社 札総</description></item>
          <item><title>会社検索</title><link>https://www.hotfrog.jp/company/1123343063875584</link><description>株式会社 札総</description></item>
        </channel></rss>
        """
    )

    assert choose_official_result(search_results, company_name="株式会社 札総", phone="011-1234-5678") is None


def test_search_queries_try_short_company_contact_query_first():
    queries = search_queries("株式会社 札幌昭栄", "011-233-1811")

    assert queries[0] == "株式会社 札幌昭栄 お問い合わせ"
    assert "011-233-1811" in queries[-1]


def test_enrich_row_from_pages_prefers_email_over_contact_form(tmp_path):
    raw_path = tmp_path / "official.html"
    raw_path.write_text(
        '<a href="/contact/">お問い合わせ</a> Email: recruit@sample-recruit.co.jp',
        encoding="utf-8",
    )
    row = {"record_id": "sample", "company_name": "Sample", "email": "", "contact_form_url": ""}

    result = enrich_row_from_pages(row, [("https://example.co.jp/", raw_path)])

    assert result.email == "recruit@sample-recruit.co.jp"
    assert result.contact_form_url == "https://example.co.jp/contact/"
    assert result.status == "email_found"


def test_enrich_row_from_pages_falls_back_to_contact_form(tmp_path):
    raw_path = tmp_path / "official.html"
    raw_path.write_text('<a href="/otoiawase/">お問い合わせ</a>', encoding="utf-8")
    row = {"record_id": "sample", "company_name": "Sample", "email": "", "contact_form_url": ""}

    result = enrich_row_from_pages(row, [("https://example.co.jp/company/", raw_path)])

    assert result.email == ""
    assert result.contact_form_url == "https://example.co.jp/otoiawase/"
    assert result.status == "contact_form_found"


def test_enrich_row_from_pages_filters_asset_like_emails(tmp_path):
    raw_path = tmp_path / "official.html"
    raw_path.write_text("logo@2x.png new@2x.gif info@sample.co.jp", encoding="utf-8")
    row = {"record_id": "sample", "company_name": "Sample", "email": "", "contact_form_url": ""}

    result = enrich_row_from_pages(row, [("https://sample.co.jp/", raw_path)])

    assert result.email == "info@sample.co.jp"


def test_enrich_row_from_pages_filters_placeholder_and_telemetry_emails(tmp_path):
    raw_path = tmp_path / "official.html"
    raw_path.write_text(
        "example@mail.com sample@test.com b5406b3@o60692.ingest.sentry.io",
        encoding="utf-8",
    )
    row = {"record_id": "sample", "company_name": "Sample", "email": "", "contact_form_url": ""}

    result = enrich_row_from_pages(row, [("https://sample.co.jp/", raw_path)])

    assert result.email == ""
    assert result.status == "official_site_found_no_contact"


def test_contact_links_ignores_non_http_api_and_external_non_form_provider_urls():
    html = """
    <a href="mailto:info@example.co.jp">メール</a>
    <a href="javascript:w=window.open('https://z103.secure.ne.jp/~z103076/secure/index.html')">お問い合わせ</a>
    <a href="/wp-json/oembed/1.0/embed?url=https%3A%2F%2Fexample.co.jp%2Fcontact%2F">お問い合わせ</a>
    <a href="https://www.hokkaido-np.co.jp/inquiry/">お問い合わせ</a>
    <a href="#colophon">お問い合わせ</a>
    <a href="/contact/">お問い合わせ</a>
    <a href="https://form.run/@sample">お問い合わせ</a>
    """

    assert contact_links(html, "https://example.co.jp/") == [
        "https://example.co.jp/contact/",
        "https://form.run/@sample",
    ]


def test_is_relevant_page_requires_phone_or_company_name():
    row = {"company_name": "株式会社 札幌昭栄", "phone": "011-233-1811"}

    assert is_relevant_page(row, "SAPPORO SYOUEI 株式会社 札幌昭栄", "https://sapporo-syouei.co.jp/")
    assert is_relevant_page(row, "電話 011-233-1811", "https://example.co.jp/")
    assert is_relevant_page(row, "電話(011)233-1811", "https://example.co.jp/")
    assert not is_relevant_page(row, "北海道旅行情報と観光案内", "https://www.visit-hokkaido.jp/")


def test_write_chinese_csv_uses_chinese_headers(tmp_path):
    rows = [
        {
            "record_id": "sample",
            "company_name": "Sample",
            "email": "info@example.co.jp",
            "phone": "03-1234-5678",
            "contact_form_url": "",
            "company_url": "https://example.co.jp/",
            "source_url": "https://mhlw.example/detail",
            "mhlw_source_url": "https://mhlw.example/detail",
            "license_number": "13-ユ-000001",
            "license_type": "有料職業紹介事業",
            "city_or_prefecture": "東京都",
            "classification": "recruitment_agency",
            "verification_status": "mhlw_verified",
            "confidence": "high",
            "source_accessed_at": "2026-05-21T11:00:00+08:00",
            "email_source_url": "https://example.co.jp/contact/",
            "enrichment_status": "email_found",
        }
    ]

    output = tmp_path / "zh.csv"
    write_chinese_csv(rows, output)

    with output.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [header for _field, header in CHINESE_HEADERS]
        assert list(reader)[0]["公司名称"] == "Sample"
