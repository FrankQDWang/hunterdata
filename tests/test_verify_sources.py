from scripts.verify_sources import extract_license_number, verify_sources


def test_extract_license_number_accepts_japanese_paid_placement_format():
    assert extract_license_number("許可番号 13-ユ-010101 有料職業紹介事業") == "13-ユ-010101"


def test_verify_sources_prefers_mhlw_verification():
    result = verify_sources(
        mhlw_text="事業区分 有料職業紹介事業 許可番号 13-ユ-010101",
        association_text="",
        business_text="人材紹介サービスと転職エージェント",
    )

    assert result.verification_status == "mhlw_verified"
    assert result.license_type == "有料職業紹介事業"
    assert result.license_number == "13-ユ-010101"
    assert result.confidence == "high"


def test_verify_sources_infers_paid_license_type_from_number():
    result = verify_sources(
        mhlw_text="許可・届出受理番号 13-ユ-305810",
        association_text="",
        business_text="職業紹介事業",
    )

    assert result.license_type == "有料職業紹介事業"


def test_verify_sources_accepts_association_directory():
    result = verify_sources(
        mhlw_text="",
        association_text="JESRA member company executive search",
        business_text="executive search recruitment",
    )

    assert result.verification_status == "association_verified"
    assert result.confidence == "high"


def test_verify_sources_uses_business_keywords_when_official_sources_absent():
    result = verify_sources(
        mhlw_text="",
        association_text="",
        business_text="転職エージェントと人材紹介を提供しています",
    )

    assert result.verification_status == "business_keyword_verified"
    assert result.confidence == "medium"


def test_verify_sources_marks_unknown_business_for_manual_review():
    result = verify_sources(mhlw_text="", association_text="", business_text="company profile")

    assert result.verification_status == "needs_manual_review"
    assert result.confidence == "low"
