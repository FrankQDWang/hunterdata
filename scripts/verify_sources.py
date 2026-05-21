from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.extract_contacts import extract_keywords


LICENSE_RE = re.compile(r"\b\d{2}-[ユﾕ]-\d{6}\b")


@dataclass(frozen=True)
class VerificationResult:
    verification_status: str
    license_number: str
    license_type: str
    confidence: str


def extract_license_number(text: str) -> str:
    match = LICENSE_RE.search(text)
    return match.group(0).replace("ﾕ", "ユ") if match else ""


def _license_type(text: str) -> str:
    if "有料職業紹介事業" in text:
        return "有料職業紹介事業"
    if "無料職業紹介事業" in text:
        return "無料職業紹介事業"
    license_number = extract_license_number(text)
    if "-ユ-" in license_number:
        return "有料職業紹介事業"
    return ""


def _is_mhlw_verified(text: str) -> bool:
    return bool(
        extract_license_number(text)
        or "有料職業紹介事業" in text
        or "無料職業紹介事業" in text
        or "職業紹介事業" in text
    )


def _is_association_verified(text: str) -> bool:
    lowered = text.casefold()
    return any(
        token.casefold() in lowered
        for token in (
            "jesra",
            "日本エグゼクティブサーチ",
            "職業紹介優良事業者",
            "適正な有料職業紹介事業者",
            "member company",
        )
    )


def verify_sources(mhlw_text: str, association_text: str, business_text: str) -> VerificationResult:
    if _is_mhlw_verified(mhlw_text):
        return VerificationResult(
            verification_status="mhlw_verified",
            license_number=extract_license_number(mhlw_text),
            license_type=_license_type(mhlw_text),
            confidence="high",
        )
    if _is_association_verified(association_text):
        return VerificationResult(
            verification_status="association_verified",
            license_number="",
            license_type="",
            confidence="high",
        )
    if extract_keywords(business_text):
        return VerificationResult(
            verification_status="business_keyword_verified",
            license_number="",
            license_type="",
            confidence="medium",
        )
    return VerificationResult(
        verification_status="needs_manual_review",
        license_number="",
        license_type="",
        confidence="low",
    )
