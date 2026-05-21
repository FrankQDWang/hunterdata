from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from scripts.contact_schema import FIELDNAMES
from scripts.extract_contacts import extract_contact_forms, extract_emails


EXTRA_FIELDS = [
    "email_source_url",
    "email_source_text_path",
    "enrichment_status",
]

ENRICHED_FIELDNAMES = FIELDNAMES + EXTRA_FIELDS

CHINESE_HEADERS = [
    ("record_id", "记录ID"),
    ("company_name", "公司名称"),
    ("contact_name", "联系人"),
    ("title", "职位"),
    ("email", "邮箱"),
    ("phone", "电话"),
    ("contact_form_url", "联系表单链接"),
    ("company_url", "公司官网"),
    ("source_url", "联系方式来源URL"),
    ("mhlw_source_url", "厚生劳动省来源URL"),
    ("license_number", "许可证号"),
    ("license_type", "许可类型"),
    ("city_or_prefecture", "城市或都道府县"),
    ("specialization", "专业领域"),
    ("classification", "业务分类"),
    ("hunter_likelihood", "猎头匹配度"),
    ("hunter_likelihood_reason", "猎头匹配理由"),
    ("evidence_keywords", "证据关键词"),
    ("verification_status", "验证状态"),
    ("confidence", "可信度"),
    ("source_accessed_at", "来源访问时间"),
    ("notes", "备注"),
    ("email_source_url", "邮箱或表单来源URL"),
    ("email_source_text_path", "邮箱或表单原始页面路径"),
    ("enrichment_status", "邮箱补充状态"),
]

DIRECTORY_DOMAINS = {
    "bing.com",
    "duckduckgo.com",
    "mapion.co.jp",
    "biz-maps.com",
    "data-link-plus.com",
    "salesnow.jp",
    "baseconnect.in",
    "houjin.jp",
    "houjinbank.com",
    "houjin.info",
    "presspage.biz",
    "job-posting.jp",
    "wantedly.com",
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "haizenstaff.net",
    "b-mall.ne.jp",
    "jassa.or.jp",
    "houjin.goo.to",
    "zinzai-haken.com",
    "ivry.jp",
    "jpnumber.com",
    "sosou.de",
    "hssa.or.jp",
    "qualityhokkaido.com",
    "nanae.jp",
    "kaisharesearch.com",
    "kensetumap.com",
    "navitime.co.jp",
    "itp.ne.jp",
    "ekiten.jp",
    "hotfrog.jp",
    "b2b-ch.infomart.co.jp",
    "map.yahoo.co.jp",
    "maps.google.com",
    "google.com",
    "visit-hokkaido.jp",
    "city.sapporo.jp",
    "myoji-yurai.net",
    "play.google.com",
    "youtube.com",
    "youtu.be",
    "jinzai.hellowork.mhlw.go.jp",
}

ALLOWED_EXTERNAL_FORM_DOMAINS = {
    "form.run",
    "form-mailer.jp",
    "formzu.net",
    "secure.ne.jp",
    "tayori.com",
    "select-type.com",
}

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SEARCH_TIMEOUT_SECONDS = 8
PAGE_TIMEOUT_SECONDS = 10

HIGH_HUNTER_KEYWORDS = [
    "Executive Search",
    "executive search",
    "エグゼクティブサーチ",
    "ヘッドハンティング",
    "ヘッドハンター",
    "CxO",
    "ＣxＯ",
    "役員紹介",
    "経営幹部",
    "幹部人材",
    "管理職紹介",
    "ハイクラス転職",
]

MEDIUM_HUNTER_KEYWORDS = [
    "人材紹介",
    "転職支援",
    "正社員採用",
    "正社員紹介",
    "中途採用",
    "採用支援",
    "キャリア支援",
    "転職エージェント",
]

EXCLUDE_HUNTER_KEYWORDS = [
    "介護",
    "看護",
    "保育",
    "家政婦",
    "配ぜん",
    "配膳",
    "技能実習",
    "特定技能",
    "派遣スタッフ",
    "人材派遣",
    "清掃",
    "警備",
    "ドライバー",
    "運転手",
    "アルバイト",
]


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    description: str


@dataclass(frozen=True)
class HunterLikelihood:
    likelihood: str
    reason: str


@dataclass(frozen=True)
class EnrichmentResult:
    email: str
    contact_form_url: str
    company_url: str
    source_url: str
    source_text_path: str
    status: str
    hunter_likelihood: str
    hunter_likelihood_reason: str


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.text_parts: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {name: value or "" for name, value in attrs}
        if tag == "a":
            self._href = data.get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, normalize_text(" ".join(self._text))))
            self._href = None
            self._text = []

    @property
    def text(self) -> str:
        return normalize_text(" ".join(self.text_parts))


class DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {name: value or "" for name, value in attrs}
        classes = data.get("class", "").split()
        if tag == "a" and "result__a" in classes:
            self._href = data.get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, normalize_text(" ".join(self._text))))
            self._href = None
            self._text = []


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def parse_bing_rss(raw_xml: str) -> list[SearchResult]:
    root = ET.fromstring(raw_xml)
    results = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        url = item.findtext("link") or ""
        description = item.findtext("description") or ""
        if url:
            results.append(SearchResult(title=title, url=url, description=description))
    return results


def parse_duckduckgo_html(raw_html: str) -> list[SearchResult]:
    parser = DuckDuckGoParser()
    parser.feed(raw_html)
    results = []
    for href, title in parser.links:
        url = _decode_duckduckgo_href(href)
        if url:
            results.append(SearchResult(title=title, url=url, description=""))
    return results


def _decode_duckduckgo_href(href: str) -> str:
    absolute = urllib.parse.urljoin("https://duckduckgo.com/", href)
    parsed = urllib.parse.urlparse(absolute)
    if parsed.netloc.casefold().removeprefix("www.") != "duckduckgo.com":
        return absolute
    params = urllib.parse.parse_qs(parsed.query)
    target = params.get("uddg", [""])[0]
    return target or absolute


def choose_official_result(results: list[SearchResult], *, company_name: str, phone: str) -> SearchResult | None:
    ranked = rank_official_results(results, company_name=company_name, phone=phone)
    return ranked[0] if ranked else None


def rank_official_results(results: list[SearchResult], *, company_name: str, phone: str) -> list[SearchResult]:
    normalized_company = _company_tokens(company_name)
    scored = []
    for index, result in enumerate(results):
        parsed = urllib.parse.urlparse(result.url)
        host = parsed.netloc.casefold().split(":", 1)[0].removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or _is_directory_like_url(parsed):
            continue
        title = result.title.casefold()
        description = result.description.casefold()
        url_text = result.url.casefold()
        phone_digits = re.sub(r"\D+", "", phone)
        result_digits = re.sub(r"\D+", "", f"{result.title} {result.description}")
        score = 0
        token_matches_title = any(token and token.casefold() in title for token in normalized_company)
        token_matches_description = any(token and token.casefold() in description for token in normalized_company)
        token_matches_url = any(token and token.casefold() in url_text for token in normalized_company)
        if token_matches_title:
            score += 6
        if token_matches_url:
            score += 3
        if token_matches_description:
            score += 1
        if phone and phone in title:
            score += 2
        elif phone and phone in description:
            score += 1
        elif len(phone_digits) >= 9 and phone_digits in result_digits:
            score += 1
        if "公式" in title or "official" in title:
            score += 2
        if any(word in f"{title} {url_text}" for word in ("お問い合わせ", "contact", "会社概要", "人材", "職業紹介")):
            score += 1
        if score > 0:
            scored.append((score, -index, result))
    if not scored:
        return []
    scored.sort(reverse=True)
    return [result for _score, _index, result in scored]


def _is_directory_host(host: str) -> bool:
    host = host.casefold().split(":", 1)[0].removeprefix("www.")
    directory_host_keywords = (
        "houjin",
        "kaisha",
        "navitime",
        "jpnumber",
        "telsearch",
        "mapion",
        "biz-maps",
        "baseconnect",
        "salesnow",
        "presspage",
        "kensetumap",
    )
    return (
        host in DIRECTORY_DOMAINS
        or any(host.endswith(f".{domain}") for domain in DIRECTORY_DOMAINS)
        or any(keyword in host for keyword in directory_host_keywords)
    )


def _is_directory_like_url(parsed: urllib.parse.ParseResult) -> bool:
    host = _normalized_host(parsed.netloc)
    if _is_directory_host(host):
        return True
    path = parsed.path.casefold()
    query = parsed.query.casefold()
    directory_path_tokens = (
        "/corporation/",
        "/corporations/",
        "/company/detail/",
        "/numberinfo_",
        "/telsearch/",
        "/poi",
        "spot=",
        "/profile.php",
    )
    return any(token in f"{path} {query}" for token in directory_path_tokens)


def _company_tokens(company_name: str) -> list[str]:
    cleaned = (
        company_name.replace("株式会社", "")
        .replace("有限会社", "")
        .replace("合同会社", "")
        .replace("　", " ")
        .strip()
    )
    tokens = [token for token in re.split(r"\s+", cleaned) if len(token) >= 2]
    compact = cleaned.replace(" ", "")
    if len(compact) >= 2:
        tokens.append(compact)
    return tokens


def fetch_text(url: str, *, timeout: int = 20, user_agent: str = "hunterdata-contact-enrichment/0.1") -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(content_type, errors="replace")


def search_queries(company_name: str, phone: str) -> list[str]:
    compact = re.sub(r"\s+", "", company_name.replace("　", " "))
    queries = [
        f"{company_name} お問い合わせ",
        f"{company_name} メール",
    ]
    if compact != company_name:
        queries.extend([f"{compact} お問い合わせ", f"{compact} メール"])
    if phone:
        queries.append(f"{company_name} {phone}")
    return _dedupe(queries)


def bing_search(company_name: str, phone: str) -> list[SearchResult]:
    all_results = []
    seen_urls = set()
    for query in search_queries(company_name, phone)[:2]:
        url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
        try:
            results = parse_bing_rss(fetch_text(url, timeout=SEARCH_TIMEOUT_SECONDS))
        except Exception:
            continue
        for result in results:
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            all_results.append(result)
        if choose_official_result(all_results, company_name=company_name, phone=phone):
            break
    return all_results


def duckduckgo_search(company_name: str, phone: str) -> list[SearchResult]:
    all_results = []
    seen_urls = set()
    for query in search_queries(company_name, phone)[:3]:
        url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        try:
            results = parse_duckduckgo_html(
                fetch_text(url, timeout=SEARCH_TIMEOUT_SECONDS, user_agent=BROWSER_USER_AGENT)
            )
        except Exception:
            continue
        for result in results:
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            all_results.append(result)
        if choose_official_result(all_results, company_name=company_name, phone=phone):
            break
    return all_results


def web_search(company_name: str, phone: str) -> list[SearchResult]:
    duckduckgo_results = duckduckgo_search(company_name, phone)
    if choose_official_result(duckduckgo_results, company_name=company_name, phone=phone):
        return duckduckgo_results
    return _merge_search_results([*duckduckgo_results, *bing_search(company_name, phone)])


def contact_links(raw_html: str, base_url: str) -> list[str]:
    parser = LinkParser()
    parser.feed(raw_html)
    candidates = extract_contact_forms(raw_html, base_url=base_url)
    for href, text in parser.links:
        absolute = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(absolute)
        path = parsed.path.casefold()
        has_contactish_url = _has_contactish_path_or_query(parsed)
        label = text.casefold()
        has_contactish_label = any(
            token in label for token in ("お問い合わせ", "お問合せ", "問合せ", "contact", "inquiry", "メール")
        )
        if has_contactish_url or has_contactish_label:
            candidates.append(absolute)
    return _dedupe([url for url in candidates if _is_trusted_contact_link(url, base_url)])


def _is_trusted_contact_link(url: str, base_url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = _normalized_host(parsed.netloc)
    if _is_directory_like_url(parsed):
        return False
    path = parsed.path.casefold()
    if any(token in path for token in ("/wp-json/", "/api/", "/cdn-cgi/", "email-protection")):
        return False
    base_host = _normalized_host(urllib.parse.urlparse(base_url).netloc)
    has_contactish_url = _has_contactish_path_or_query(parsed)
    if _is_allowed_external_form_host(host):
        return True
    if not _is_same_site(host, base_host):
        return False
    return has_contactish_url


def _has_contactish_path_or_query(parsed: urllib.parse.ParseResult) -> bool:
    haystack = f"{parsed.path} {parsed.query}".casefold()
    return any(token in haystack for token in ("contact", "inquiry", "otoiawase", "toiawase", "お問い合わせ"))


def _normalized_host(netloc: str) -> str:
    return netloc.casefold().split(":", 1)[0].removeprefix("www.")


def _is_same_site(host: str, base_host: str) -> bool:
    return bool(host and base_host) and (
        host == base_host or host.endswith(f".{base_host}") or base_host.endswith(f".{host}")
    )


def _is_allowed_external_form_host(host: str) -> bool:
    return host in ALLOWED_EXTERNAL_FORM_DOMAINS or any(
        host.endswith(f".{domain}") for domain in ALLOWED_EXTERNAL_FORM_DOMAINS
    )


def classify_hunter_likelihood(text: str) -> HunterLikelihood:
    normalized = normalize_text(text)
    if match := _first_keyword(normalized, HIGH_HUNTER_KEYWORDS):
        return HunterLikelihood("high", f"Official/public evidence contains strong hunter signal: {match}.")
    if match := _first_keyword(normalized, EXCLUDE_HUNTER_KEYWORDS):
        return HunterLikelihood("exclude", f"Official/public evidence is mainly non-headhunter vertical: {match}.")
    if match := _first_keyword(normalized, MEDIUM_HUNTER_KEYWORDS):
        return HunterLikelihood("medium", f"Official/public evidence describes recruitment/placement service: {match}.")
    return HunterLikelihood("low", "No explicit headhunter or recruiting-positioning signal found in available official/public evidence.")


def _first_keyword(text: str, keywords: list[str]) -> str:
    haystack = text.casefold()
    for keyword in keywords:
        if keyword.casefold() in haystack:
            return keyword
    return ""


def enrich_row_from_pages(row: dict[str, str], pages: list[tuple[str, Path]]) -> EnrichmentResult:
    first_company_url = pages[0][0] if pages else row.get("company_url", "")
    evidence_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for _page_url, path in pages)
    hunter = classify_hunter_likelihood(evidence_text) if evidence_text else HunterLikelihood(
        row.get("hunter_likelihood", "low") or "low",
        row.get("hunter_likelihood_reason", "MHLW license only; no official site evidence evaluated yet.")
        or "MHLW license only; no official site evidence evaluated yet.",
    )
    best_form = ""
    best_source = ""
    best_path = ""
    for page_url, path in pages:
        raw_html = path.read_text(encoding="utf-8", errors="replace")
        emails = _filter_business_emails(extract_emails(raw_html))
        forms = contact_links(raw_html, page_url)
        if forms and not best_form:
            best_form = forms[0]
            best_source = page_url
            best_path = str(path)
        if emails:
            return EnrichmentResult(
                email=emails[0],
                contact_form_url=forms[0] if forms else best_form,
                company_url=first_company_url,
                source_url=page_url,
                source_text_path=str(path),
                status="email_found",
                hunter_likelihood=hunter.likelihood,
                hunter_likelihood_reason=hunter.reason,
            )
    if best_form:
        return EnrichmentResult(
            email="",
            contact_form_url=best_form,
            company_url=first_company_url,
            source_url=best_source,
            source_text_path=best_path,
            status="contact_form_found",
            hunter_likelihood=hunter.likelihood,
            hunter_likelihood_reason=hunter.reason,
        )
    return EnrichmentResult(
        email="",
        contact_form_url="",
        company_url=first_company_url,
        source_url=first_company_url,
        source_text_path=str(pages[0][1]) if pages else "",
        status="official_site_found_no_contact" if pages else "not_found",
        hunter_likelihood=hunter.likelihood,
        hunter_likelihood_reason=hunter.reason,
    )


def _filter_business_emails(emails: list[str]) -> list[str]:
    rejected_hosts = {"example.com", "example.co.jp", "test.com", "mail.com"}
    rejected_prefixes = {"privacy", "abuse", "postmaster", "webmaster", "noreply", "no-reply"}
    rejected_locals = {"example", "sample", "test", "dummy", "taroyamada", "taro.yamada", "yamada.taro"}
    rejected_tlds = {"gif", "png", "jpg", "jpeg", "svg", "webp", "css", "js"}
    kept = []
    for email in emails:
        local, _, host = email.partition("@")
        local = local.casefold()
        host = host.casefold()
        host_parts = host.rsplit(".", 1)
        tld = host_parts[-1] if len(host_parts) == 2 else ""
        if (
            host in rejected_hosts
            or host.endswith(".sentry.io")
            or "ingest.sentry.io" in host
            or local in rejected_prefixes
            or local in rejected_locals
            or tld in rejected_tlds
        ):
            continue
        kept.append(email)
    return kept


def is_relevant_page(row: dict[str, str], raw_html: str, url: str) -> bool:
    text = normalize_text(raw_html).replace("　", " ")
    compact_text = re.sub(r"\s+", "", text)
    phone = row.get("phone", "").strip()
    phone_digits = re.sub(r"\D+", "", phone)
    text_digits = re.sub(r"\D+", "", text)
    if phone and (phone in text or (len(phone_digits) >= 9 and phone_digits in text_digits)):
        return True

    company_name = row.get("company_name", "")
    compact_company = re.sub(r"\s+", "", company_name.replace("　", " "))
    if len(compact_company) >= 4 and compact_company in compact_text:
        return True

    tokens = [token for token in _company_tokens(company_name) if len(token) >= 4]
    haystack = f"{text} {url}".casefold()
    return any(token.casefold() in haystack for token in tokens)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _merge_search_results(results: list[SearchResult]) -> list[SearchResult]:
    seen = set()
    merged = []
    for result in results:
        if result.url in seen:
            continue
        seen.add(result.url)
        merged.append(result)
    return merged


def safe_raw_path(raw_dir: Path, record_id: str, url: str, suffix: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return raw_dir / f"{record_id}-{suffix}-{digest}.html"


def static_enrich_row(row: dict[str, str], raw_dir: Path) -> dict[str, str]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    pages: list[tuple[str, Path]] = []
    company_url = row.get("company_url", "").strip()
    if company_url:
        urls = [company_url]
    else:
        results = web_search(row["company_name"], row.get("phone", ""))
        urls = [
            result.url
            for result in rank_official_results(results, company_name=row["company_name"], phone=row.get("phone", ""))[:1]
        ]

    for index, url in enumerate(urls):
        try:
            raw_html = fetch_text(url, timeout=PAGE_TIMEOUT_SECONDS)
        except Exception:
            continue
        if not is_relevant_page(row, raw_html, url):
            continue
        path = safe_raw_path(raw_dir, row["record_id"], url, f"page{index + 1}")
        path.write_text(raw_html, encoding="utf-8")
        pages.append((url, path))

        for contact_url in contact_links(raw_html, url)[:2]:
            try:
                contact_html = fetch_text(contact_url, timeout=PAGE_TIMEOUT_SECONDS)
            except Exception:
                continue
            contact_path = safe_raw_path(raw_dir, row["record_id"], contact_url, "contact")
            contact_path.write_text(contact_html, encoding="utf-8")
            pages.append((contact_url, contact_path))
            if _filter_business_emails(extract_emails(contact_html)):
                break

    result = enrich_row_from_pages(row, pages)
    updated = {field: row.get(field, "") for field in FIELDNAMES}
    if result.email:
        updated["email"] = result.email
    if result.contact_form_url:
        updated["contact_form_url"] = result.contact_form_url
    if result.company_url:
        updated["company_url"] = result.company_url
    updated.update(
        {
            "email_source_url": result.source_url,
            "email_source_text_path": result.source_text_path,
            "enrichment_status": result.status,
            "hunter_likelihood": result.hunter_likelihood,
            "hunter_likelihood_reason": result.hunter_likelihood_reason,
        }
    )
    return updated


def enrich_contacts(input_csv: Path, output_csv: Path, zh_output_csv: Path, raw_dir: Path, *, sleep_seconds: float) -> None:
    with input_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    enriched_rows = []
    total = len(rows)
    for row_number, row in enumerate(rows, start=1):
        updated = static_enrich_row(row, raw_dir)
        enriched_rows.append(updated)
        print(
            f"[{row_number}/{total}] {row.get('company_name', '')}: {updated.get('enrichment_status', '')}",
            file=sys.stderr,
            flush=True,
        )
        if sleep_seconds:
            time.sleep(sleep_seconds)

    write_enriched_csv(enriched_rows, output_csv)
    write_chinese_csv(enriched_rows, zh_output_csv)


def write_enriched_csv(rows: list[dict[str, str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ENRICHED_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in ENRICHED_FIELDNAMES})


def write_chinese_csv(rows: list[dict[str, str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=[header for _field, header in CHINESE_HEADERS])
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(field, "") for field, header in CHINESE_HEADERS})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/processed/japan_headhunters_contacts.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/japan_headhunters_contacts_enriched.csv"))
    parser.add_argument("--zh-output", type=Path, default=Path("data/processed/japan_headhunters_contacts_enriched_zh.csv"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/enrichment"))
    parser.add_argument("--sleep-seconds", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    enrich_contacts(
        input_csv=args.input,
        output_csv=args.output,
        zh_output_csv=args.zh_output,
        raw_dir=args.raw_dir,
        sleep_seconds=args.sleep_seconds,
    )
    print(args.output)
    print(args.zh_output)


if __name__ == "__main__":
    main()
