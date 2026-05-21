from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse


EXECUTIVE_KEYWORDS = [
    "ヘッドハンティング",
    "エグゼクティブサーチ",
    "executive search",
    "headhunting",
    "スカウト",
]
RECRUITMENT_KEYWORDS = [
    "人材紹介",
    "職業紹介",
    "転職エージェント",
    "採用支援",
    "recruitment",
    "placement",
]
STAFFING_KEYWORDS = ["人材派遣", "派遣", "staffing"]
HR_CONSULTING_KEYWORDS = ["hr consulting", "HR consulting", "アウトソーシング", "業務委託"]
ALL_KEYWORDS = EXECUTIVE_KEYWORDS + RECRUITMENT_KEYWORDS + STAFFING_KEYWORDS + HR_CONSULTING_KEYWORDS

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
PHONE_RE = re.compile(r"(?<!\d)(0\d{1,4}[-\s]\d{1,4}[-\s]\d{3,4})(?!\d)")
ABSOLUTE_URL_RE = re.compile(r"https?://[^\s<>'\")]+", re.IGNORECASE)
RELATIVE_CONTACT_RE = re.compile(
    r"(?<!:)/(?:contact|contacts|inquiry|inquiries|toiawase|otoiawase|お問い合わせ)[^\s<>'\")]*",
    re.IGNORECASE,
)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def extract_emails(text: str) -> list[str]:
    return _dedupe([match.group(0).lower() for match in EMAIL_RE.finditer(text)])


def extract_phones(text: str) -> list[str]:
    phones = []
    for line in text.splitlines():
        normalized_line = line.replace("ＦＡＸ", "FAX").replace("ＴＥＬ", "TEL")
        if re.search(r"\bFAX\b|ファックス", normalized_line, re.IGNORECASE) and not re.search(
            r"\bTEL\b|電話", normalized_line, re.IGNORECASE
        ):
            continue
        for match in PHONE_RE.finditer(normalized_line):
            prefix = normalized_line[max(0, match.start() - 16) : match.start()]
            if re.search(r"FAX|ファックス", prefix, re.IGNORECASE):
                continue
            phones.append(re.sub(r"\s+", "-", match.group(1).strip()))
    return _dedupe(phones)


def _is_contact_url(url: str) -> bool:
    parsed = urlparse(url)
    haystack = f"{parsed.path} {parsed.query}".lower()
    return any(
        token in haystack
        for token in (
            "contact",
            "contacts",
            "inquiry",
            "inquiries",
            "toiawase",
            "otoiawase",
            "お問い合わせ",
        )
    )


def extract_contact_forms(text: str, base_url: str | None = None) -> list[str]:
    urls = [match.group(0).rstrip(".,;") for match in ABSOLUTE_URL_RE.finditer(text)]
    if base_url:
        urls.extend(urljoin(base_url, match.group(0).rstrip(".,;")) for match in RELATIVE_CONTACT_RE.finditer(text))
    return _dedupe([url for url in urls if _is_contact_url(url)])


def extract_keywords(text: str) -> list[str]:
    lowered = text.casefold()
    found = []
    for keyword in ALL_KEYWORDS:
        if keyword.casefold() in lowered:
            found.append(keyword)
    return _dedupe(found)


def classify_business(texts: list[str]) -> str:
    text = "\n".join(texts)
    lowered = text.casefold()
    has_executive = any(keyword.casefold() in lowered for keyword in EXECUTIVE_KEYWORDS)
    has_recruitment = any(keyword.casefold() in lowered for keyword in RECRUITMENT_KEYWORDS)
    has_staffing = any(keyword.casefold() in lowered for keyword in STAFFING_KEYWORDS)
    has_hr_consulting = any(keyword.casefold() in lowered for keyword in HR_CONSULTING_KEYWORDS)

    categories = [
        has_executive,
        has_recruitment,
        has_staffing,
        has_hr_consulting,
    ]
    if sum(1 for present in categories if present) > 1:
        return "mixed_hr_service"
    if has_executive:
        return "executive_search"
    if has_recruitment:
        return "recruitment_agency"
    if has_staffing:
        return "staffing_or_dispatch"
    return "unknown"
