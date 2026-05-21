from scripts.extract_contacts import (
    classify_business,
    extract_contact_forms,
    extract_emails,
    extract_keywords,
    extract_phones,
)


def test_extract_emails_deduplicates_and_lowercases():
    text = "Contact INFO@Example.co.jp or info@example.co.jp for 人材紹介."

    assert extract_emails(text) == ["info@example.co.jp"]


def test_extract_phones_excludes_fax_numbers():
    text = """
    TEL: 03-1234-5678
    FAX: 03-9999-0000
    電話 06-1111-2222
    """

    assert extract_phones(text) == ["03-1234-5678", "06-1111-2222"]


def test_extract_contact_forms_returns_absolute_urls_from_relative_paths():
    text = "お問い合わせはこちら: /contact/  Careers: https://example.com/jobs"

    assert extract_contact_forms(text, base_url="https://example.com/company") == [
        "https://example.com/contact/"
    ]


def test_extract_keywords_and_classification_for_executive_search():
    text = "弊社はエグゼクティブサーチとヘッドハンティングを提供します。"

    assert extract_keywords(text) == ["ヘッドハンティング", "エグゼクティブサーチ"]
    assert classify_business([text]) == "executive_search"


def test_classify_business_unknown_without_keywords():
    assert classify_business(["会社概要と住所だけが掲載されています。"]) == "unknown"


def test_classify_business_mixed_when_multiple_categories_appear():
    text = "人材紹介、採用支援、人材派遣、HR consulting services"

    assert classify_business([text]) == "mixed_hr_service"
