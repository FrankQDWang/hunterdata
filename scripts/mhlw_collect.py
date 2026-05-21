from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from scripts.collect_contacts import QualityGateError, load_candidate_records, write_accepted_records
from scripts.extract_contacts import extract_phones
from scripts.verify_sources import extract_license_number


BASE_URL = "https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/"
SEARCH_URL = urllib.parse.urljoin(BASE_URL, "GICB102030.do")


@dataclass(frozen=True)
class SearchPage:
    total_count: int
    current_page: int
    detail_urls: list[str]
    hf_cond: str


@dataclass(frozen=True)
class MhlwDetail:
    source_url: str
    license_number: str
    license_type: str
    company_name: str
    office_name: str
    phone: str
    company_url: str
    address: str
    city_or_prefecture: str


class TextAndLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {name: value or "" for name, value in attrs}
        if tag == "a":
            self._current_href = data.get("href")
            self._current_text = []
        elif tag == "input" and data.get("name"):
            self.inputs[data["name"]] = data.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href:
            text = normalize_text(" ".join(self._current_text))
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._current_href is not None:
            self._current_text.append(data)

    @property
    def text(self) -> str:
        return normalize_text(" ".join(self.text_parts))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value).replace("\xa0", " ")).strip()


def _first_int(pattern: str, text: str, default: int = 0) -> int:
    match = re.search(pattern, text, flags=re.DOTALL)
    return int(match.group(1)) if match else default


def parse_search_results(raw_html: str, *, base_url: str = BASE_URL) -> SearchPage:
    parser = TextAndLinkParser()
    parser.feed(raw_html)
    detail_urls = []
    seen = set()
    for href, _text in parser.links:
        if "action=detail" not in href or "detkey_Detail=" not in href:
            continue
        absolute = urllib.parse.urljoin(base_url, href.replace("&amp;", "&"))
        if absolute in seen:
            continue
        seen.add(absolute)
        detail_urls.append(absolute)
    return SearchPage(
        total_count=_first_int(r'id="ID_lbSearchCount"[^>]*>\s*(\d+)', raw_html),
        current_page=_first_int(r'id="ID_lbSearchCurrentPage"[^>]*>\s*(\d+)', raw_html, default=1),
        detail_urls=detail_urls,
        hf_cond=parser.inputs.get("hfCond", ""),
    )


def parse_detail_page(raw_html: str, *, source_url: str) -> MhlwDetail:
    parser = TextAndLinkParser()
    parser.feed(raw_html)
    text = parser.text
    license_number = extract_license_number(text)
    company_name = _between(text, "事業主名称", "事業所名称")
    office_name = _between(text, "事業所名称", "事業所所在地")
    address = _between(text, "事業所所在地", "電話番号")
    phones = extract_phones(text)
    company_url = _company_url(parser.links)
    return MhlwDetail(
        source_url=source_url,
        license_number=license_number,
        license_type="有料職業紹介事業" if "-ユ-" in license_number else "",
        company_name=company_name or office_name,
        office_name=office_name,
        phone=phones[0] if phones else "",
        company_url=company_url,
        address=address,
        city_or_prefecture=_prefecture(address),
    )


def _between(text: str, start: str, end: str) -> str:
    match = re.search(re.escape(start) + r"\s*(.*?)\s*" + re.escape(end), text)
    return normalize_text(match.group(1)) if match else ""


def _company_url(links: list[tuple[str, str]]) -> str:
    for href, text in links:
        absolute = urllib.parse.urljoin(BASE_URL, href)
        host = urllib.parse.urlparse(absolute).netloc
        if host and "jinzai.hellowork.mhlw.go.jp" not in host and text not in {"有", ""}:
            return absolute
    return ""


def _prefecture(address: str) -> str:
    match = re.match(r"(.{2,3}[都道府県])", address)
    return match.group(1) if match else ""


def search_payload(*, action: str, params: str = "", hf_cond: str = "", page: int = 1) -> bytes:
    values = {
        "params": params,
        "screenId": "GICB102030",
        "action": action,
        "curPage": str(page),
        "hfSortKey": "KEYSHUSHOKUSHA1YUKI3",
        "hfSortOrder": "ASC",
        "hfSortOrderRev": "DESC",
        "cbZenkoku": "1",
        "cbJigyoshoKbnYu": "1",
        "cbJigyonushiName": "1",
        "cbJigyoshoName": "1",
        "hfScrollTop": "0",
    }
    if hf_cond:
        values["hfCond"] = hf_cond
    return urllib.parse.urlencode(values).encode("utf-8")


def fetch_url(url: str, *, data: bytes | None = None, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": "hunterdata-contact-research/0.1"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _raw_path(raw_dir: Path, prefix: str, page_or_index: int, detail: MhlwDetail | None = None) -> Path:
    if detail and detail.license_number:
        safe = detail.license_number.replace("-", "_")
    else:
        safe = str(page_or_index)
    return raw_dir / f"{prefix}-{page_or_index:04d}-{safe}.html"


def collect_from_mhlw(
    *,
    target_count: int,
    raw_dir: Path,
    output_dir: Path,
    interim_path: Path = Path("data/interim/candidates.jsonl"),
    sleep_seconds: float = 0.1,
) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    interim_path.parent.mkdir(parents=True, exist_ok=True)

    candidates: list[dict[str, str]] = []
    seen_detail_urls: set[str] = set()
    accessed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    page_number = 1

    search_html = fetch_url(SEARCH_URL, data=search_payload(action="search"))
    (raw_dir / "search-page-1.html").write_text(search_html, encoding="utf-8")
    search_page = parse_search_results(search_html)
    hf_cond = search_page.hf_cond

    while True:
        if page_number == 1:
            current_page = search_page
        else:
            page_html = fetch_url(SEARCH_URL, data=search_payload(action="page", params=str(page_number), hf_cond=hf_cond))
            (raw_dir / f"search-page-{page_number}.html").write_text(page_html, encoding="utf-8")
            current_page = parse_search_results(page_html)
        if not current_page.detail_urls:
            raise QualityGateError(f"MHLW search page {page_number} returned no detail URLs")

        for detail_url in current_page.detail_urls:
            if detail_url in seen_detail_urls:
                continue
            seen_detail_urls.add(detail_url)
            detail_html = fetch_url(detail_url)
            detail = parse_detail_page(detail_html, source_url=detail_url)
            if not detail.company_name or not detail.phone or not detail.license_number:
                continue
            detail_path = _raw_path(raw_dir, "detail", len(candidates) + 1, detail)
            detail_path.write_text(detail_html, encoding="utf-8")
            candidates.append(
                {
                    "company_name": detail.company_name,
                    "company_url": detail.company_url,
                    "source_url": detail.source_url,
                    "source_text_path": str(detail_path),
                    "mhlw_text_path": str(detail_path),
                    "mhlw_source_url": detail.source_url,
                    "association_text_path": "",
                    "city_or_prefecture": detail.city_or_prefecture,
                    "accessed_at": accessed_at,
                    "notes": "Contact phone sourced from MHLW public occupational placement business detail page.",
                }
            )
            time.sleep(sleep_seconds)

        interim_path.write_text(
            "\n".join(json.dumps(candidate, ensure_ascii=False) for candidate in candidates) + "\n",
            encoding="utf-8",
        )
        try:
            outputs = write_accepted_records(load_candidate_records(interim_path), output_dir, target_count=target_count)
            return outputs.csv_path
        except QualityGateError as exc:
            if "accepted row count" not in str(exc):
                raise
            if current_page.total_count and len(seen_detail_urls) >= current_page.total_count:
                raise
        page_number += 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-count", type=int, default=100)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/mhlw"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--sleep-seconds", type=float, default=0.1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = collect_from_mhlw(
        target_count=args.target_count,
        raw_dir=args.raw_dir,
        output_dir=args.output_dir,
        sleep_seconds=args.sleep_seconds,
    )
    print(output)


if __name__ == "__main__":
    main()
