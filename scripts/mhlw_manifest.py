from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from scripts.contact_schema import stable_record_id
from scripts.mhlw_collect import (
    SEARCH_URL,
    MhlwDetail,
    fetch_url,
    parse_detail_page,
    parse_search_results,
    search_payload,
)


MANIFEST_FIELDNAMES = [
    "record_id",
    "company_name",
    "office_name",
    "phone",
    "company_url",
    "license_number",
    "license_type",
    "city_or_prefecture",
    "address",
    "mhlw_source_url",
    "mhlw_raw_path",
    "page_number",
    "detail_index",
    "collected_at",
]


@dataclass(frozen=True)
class ManifestResult:
    csv_path: Path
    jsonl_path: Path
    checkpoint_path: Path
    records: int
    appended_records: int = 0


@dataclass(frozen=True)
class ManifestRow:
    record_id: str
    company_name: str
    office_name: str
    phone: str
    company_url: str
    license_number: str
    license_type: str
    city_or_prefecture: str
    address: str
    mhlw_source_url: str
    mhlw_raw_path: str
    page_number: str
    detail_index: str
    collected_at: str


def refresh_mhlw_manifest(
    *,
    manifest_csv: Path = Path("data/manifest/mhlw_manifest.csv"),
    manifest_jsonl: Path = Path("data/manifest/mhlw_manifest.jsonl"),
    checkpoint_path: Path = Path("data/manifest/checkpoint.json"),
    raw_dir: Path = Path("data/raw/mhlw"),
    limit: int = 0,
    max_pages: int = 0,
    sleep_seconds: float = 0.1,
) -> ManifestResult:
    manifest_csv.parent.mkdir(parents=True, exist_ok=True)
    manifest_jsonl.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    rows: list[ManifestRow] = []
    seen_detail_urls: set[str] = set()
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")

    search_html = fetch_url(SEARCH_URL, data=search_payload(action="search"))
    (raw_dir / "search-page-0001.html").write_text(search_html, encoding="utf-8")
    first_page = parse_search_results(search_html)
    hf_cond = first_page.hf_cond
    page_number = 1

    while True:
        if page_number == 1:
            current_page = first_page
        else:
            page_html = fetch_url(SEARCH_URL, data=search_payload(action="page", params=str(page_number), hf_cond=hf_cond))
            (raw_dir / f"search-page-{page_number:04d}.html").write_text(page_html, encoding="utf-8")
            current_page = parse_search_results(page_html)

        if not current_page.detail_urls:
            break

        for detail_index, detail_url in enumerate(current_page.detail_urls, start=1):
            if detail_url in seen_detail_urls:
                continue
            seen_detail_urls.add(detail_url)
            detail_html = fetch_url(detail_url)
            detail = parse_detail_page(detail_html, source_url=detail_url)
            if not _usable_detail(detail):
                continue
            detail_path = raw_dir / _detail_raw_filename(len(rows) + 1, detail)
            detail_path.write_text(detail_html, encoding="utf-8")
            rows.append(
                manifest_row_from_detail(
                    detail,
                    raw_path=detail_path,
                    page_number=page_number,
                    detail_index=detail_index,
                    collected_at=collected_at,
                )
            )
            if limit and len(rows) >= limit:
                _write_manifest_outputs(rows, manifest_csv, manifest_jsonl)
                _write_checkpoint(
                    checkpoint_path,
                    done=True,
                    page_number=page_number,
                    records=len(rows),
                    total_count=current_page.total_count,
                )
                return ManifestResult(manifest_csv, manifest_jsonl, checkpoint_path, len(rows))
            if sleep_seconds:
                time.sleep(sleep_seconds)

        _write_manifest_outputs(rows, manifest_csv, manifest_jsonl)
        done = bool(current_page.total_count and len(seen_detail_urls) >= current_page.total_count)
        _write_checkpoint(
            checkpoint_path,
            done=done,
            page_number=page_number,
            records=len(rows),
            total_count=current_page.total_count,
        )
        if done or (max_pages and page_number >= max_pages):
            break
        page_number += 1

    _write_manifest_outputs(rows, manifest_csv, manifest_jsonl)
    _write_checkpoint(
        checkpoint_path,
        done=True,
        page_number=page_number,
        records=len(rows),
        total_count=first_page.total_count,
    )
    return ManifestResult(manifest_csv, manifest_jsonl, checkpoint_path, len(rows))


def append_next_mhlw_records(
    *,
    manifest_csv: Path = Path("data/manifest/mhlw_manifest.csv"),
    manifest_jsonl: Path = Path("data/manifest/mhlw_manifest.jsonl"),
    checkpoint_path: Path = Path("data/manifest/checkpoint.json"),
    raw_dir: Path = Path("data/raw/mhlw"),
    limit: int = 100,
    sleep_seconds: float = 0.1,
) -> ManifestResult:
    manifest_csv.parent.mkdir(parents=True, exist_ok=True)
    manifest_jsonl.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_manifest_rows(manifest_csv)
    seen_detail_urls = {row.get("mhlw_source_url", "") for row in rows if row.get("mhlw_source_url")}
    page_number, detail_offset = _load_append_cursor(checkpoint_path, rows)
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    appended = 0
    full_manifest_done = False

    search_html = fetch_url(SEARCH_URL, data=search_payload(action="search"))
    first_page = parse_search_results(search_html)
    hf_cond = first_page.hf_cond
    total_count = first_page.total_count

    while limit <= 0 or appended < limit:
        if page_number == 1:
            page_html = search_html
            current_page = first_page
        else:
            page_html = fetch_url(SEARCH_URL, data=search_payload(action="page", params=str(page_number), hf_cond=hf_cond))
            current_page = parse_search_results(page_html)
        (raw_dir / f"search-page-{page_number:04d}.html").write_text(page_html, encoding="utf-8")

        if not current_page.detail_urls:
            full_manifest_done = True
            break

        while detail_offset < len(current_page.detail_urls) and (limit <= 0 or appended < limit):
            detail_url = current_page.detail_urls[detail_offset]
            detail_index = detail_offset + 1
            detail_offset += 1
            if detail_url in seen_detail_urls:
                continue
            seen_detail_urls.add(detail_url)
            detail_html = fetch_url(detail_url)
            detail = parse_detail_page(detail_html, source_url=detail_url)
            if not _usable_detail(detail):
                continue
            detail_path = raw_dir / _detail_raw_filename(len(rows) + 1, detail)
            detail_path.write_text(detail_html, encoding="utf-8")
            row = manifest_row_from_detail(
                detail,
                raw_path=detail_path,
                page_number=page_number,
                detail_index=detail_index,
                collected_at=collected_at,
            )
            rows.append(asdict(row))
            appended += 1
            if sleep_seconds:
                time.sleep(sleep_seconds)

        if detail_offset >= len(current_page.detail_urls):
            page_number += 1
            detail_offset = 0

        if current_page.total_count and len(seen_detail_urls) >= current_page.total_count:
            full_manifest_done = True
            break

    _write_manifest_dict_outputs(rows, manifest_csv, manifest_jsonl)
    _write_checkpoint(
        checkpoint_path,
        done=full_manifest_done,
        page_number=max(1, page_number - 1 if detail_offset == 0 else page_number),
        records=len(rows),
        total_count=total_count,
        next_page_number=page_number,
        next_detail_offset=detail_offset,
        full_manifest_done=full_manifest_done,
        limit_reached=bool(limit > 0 and appended >= limit),
    )
    return ManifestResult(manifest_csv, manifest_jsonl, checkpoint_path, len(rows), appended)


def manifest_row_from_detail(
    detail: MhlwDetail,
    *,
    raw_path: Path,
    page_number: int,
    detail_index: int,
    collected_at: str,
) -> ManifestRow:
    return ManifestRow(
        record_id=stable_record_id(detail.company_name, detail.source_url),
        company_name=detail.company_name,
        office_name=detail.office_name,
        phone=detail.phone,
        company_url=detail.company_url,
        license_number=detail.license_number,
        license_type=detail.license_type,
        city_or_prefecture=detail.city_or_prefecture,
        address=detail.address,
        mhlw_source_url=detail.source_url,
        mhlw_raw_path=str(raw_path),
        page_number=str(page_number),
        detail_index=str(detail_index),
        collected_at=collected_at,
    )


def _usable_detail(detail: MhlwDetail) -> bool:
    return bool(detail.company_name and detail.phone and detail.license_number)


def _detail_raw_filename(index: int, detail: MhlwDetail) -> str:
    safe = detail.license_number.replace("-", "_") if detail.license_number else "unknown"
    return f"detail-{index:06d}-{safe}.html"


def _write_manifest_outputs(rows: list[ManifestRow], csv_path: Path, jsonl_path: Path) -> None:
    _write_manifest_dict_outputs([asdict(row) for row in rows], csv_path, jsonl_path)


def _write_manifest_dict_outputs(rows: list[dict[str, str]], csv_path: Path, jsonl_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in MANIFEST_FIELDNAMES})
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({field: row.get(field, "") for field in MANIFEST_FIELDNAMES}, ensure_ascii=False, sort_keys=False) + "\n")


def _read_manifest_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _load_append_cursor(checkpoint_path: Path, rows: list[dict[str, str]]) -> tuple[int, int]:
    if checkpoint_path.exists():
        try:
            state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        page_number = int(state.get("next_page_number") or 0)
        detail_offset = int(state.get("next_detail_offset") or 0)
        if page_number >= 1 and detail_offset >= 0:
            return page_number, detail_offset
    if not rows:
        return 1, 0
    last = max(rows, key=lambda row: (int(row.get("page_number") or 0), int(row.get("detail_index") or 0)))
    page_number = int(last.get("page_number") or 1)
    detail_index = int(last.get("detail_index") or 0)
    if detail_index >= 20:
        return page_number + 1, 0
    return page_number, detail_index


def _write_checkpoint(
    path: Path,
    *,
    done: bool,
    page_number: int,
    records: int,
    total_count: int,
    next_page_number: int | None = None,
    next_detail_offset: int | None = None,
    full_manifest_done: bool | None = None,
    limit_reached: bool | None = None,
) -> None:
    payload = {
        "done": done,
        "page_number": page_number,
        "records": records,
        "total_count": total_count,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if next_page_number is not None:
        payload["next_page_number"] = next_page_number
    if next_detail_offset is not None:
        payload["next_detail_offset"] = next_detail_offset
    if full_manifest_done is not None:
        payload["full_manifest_done"] = full_manifest_done
    if limit_reached is not None:
        payload["limit_reached"] = limit_reached
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the MHLW public recruitment business manifest.")
    parser.add_argument("--manifest-csv", type=Path, default=Path("data/manifest/mhlw_manifest.csv"))
    parser.add_argument("--manifest-jsonl", type=Path, default=Path("data/manifest/mhlw_manifest.jsonl"))
    parser.add_argument("--checkpoint", type=Path, default=Path("data/manifest/checkpoint.json"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/mhlw"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0.1)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    result = refresh_mhlw_manifest(
        manifest_csv=args.manifest_csv,
        manifest_jsonl=args.manifest_jsonl,
        checkpoint_path=args.checkpoint,
        raw_dir=args.raw_dir,
        limit=args.limit,
        max_pages=args.max_pages,
        sleep_seconds=args.sleep_seconds,
    )
    print(result.csv_path)


if __name__ == "__main__":
    main()
