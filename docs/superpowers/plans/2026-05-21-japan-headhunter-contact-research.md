# Japan Headhunter Contact Research Dataset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible local workflow that collects 100 source-traceable Japan recruitment/headhunter contact records using MHLW verification, public company pages, and Dokobot reads.

**Architecture:** Use a small Python 3.12 standard-library pipeline with explicit schema validation, raw-source capture, contact extraction, MHLW/business verification, deduplication, and QA reporting. `uv` owns the Python version, virtual environment, dependency sync, and all Python command execution. Dokobot is invoked through a thin subprocess wrapper so rendered web reads are captured as auditable raw text before processing.

**Tech Stack:** Python 3.12 managed by `uv`, standard library (`csv`, `json`, `re`, `subprocess`, `urllib.parse`, `html.parser`, `datetime`), Dokobot CLI v2.x, pytest for tests.

**Spec:** `docs/superpowers/specs/2026-05-21-japan-headhunter-contact-research.md`

---

## File Structure

- Create `README.md` with setup and execution commands.
- Create `.python-version` pinned to Python 3.12.
- Create `pyproject.toml` for the `uv` managed Python project.
- Generate `uv.lock` with `uv sync --python 3.12`.
- Create `docs/research-protocol.md` with source hierarchy, compliance boundaries, and manual review rules.
- Create `data/raw/dokobot/.gitkeep` for raw Dokobot reads.
- Create `data/raw/manual_observations.md` for notes when official search pages need browser interaction.
- Create `data/interim/.gitkeep` for candidate and normalized intermediate files.
- Create `data/interim/candidates.example.jsonl` documenting the candidate manifest shape.
- Create `data/processed/.gitkeep` for final CSV, JSONL, sources CSV, and QA report.
- Create `scripts/contact_schema.py` for row field order, normalization, validation, dedup keys, and CSV/JSONL writers.
- Create `scripts/dokobot_client.py` for checked Dokobot command execution and raw output capture.
- Create `scripts/extract_contacts.py` for email, phone, contact form, keyword, and classification extraction from raw page text.
- Create `scripts/verify_sources.py` for MHLW/JESRA/business keyword verification status.
- Create `scripts/collect_contacts.py` for the orchestration CLI.
- Create `scripts/qa_report.py` for final dataset validation and report generation.
- Create tests under `tests/` for schema, extraction, verification, and QA behavior.

---

## Task 1: Project Scaffold And Research Protocol

**Files:**
- Create: `README.md`
- Create: `.python-version`
- Create: `pyproject.toml`
- Generate: `uv.lock`
- Create: `docs/research-protocol.md`
- Create: `data/raw/dokobot/.gitkeep`
- Create: `data/raw/manual_observations.md`
- Create: `data/interim/.gitkeep`
- Create: `data/interim/candidates.example.jsonl`
- Create: `data/processed/.gitkeep`

- [ ] **Step 1: Create directory scaffold**

Run:

```bash
mkdir -p docs data/raw/dokobot data/interim data/processed scripts tests
touch data/raw/dokobot/.gitkeep data/interim/.gitkeep data/processed/.gitkeep
```

Expected: directories exist and `find data -maxdepth 3 -type f` shows three `.gitkeep` files.

- [ ] **Step 2: Write Python 3.12 and `uv` project files**

Create `.python-version` with:

```text
3.12
```

Create `pyproject.toml` with:

```toml
[project]
name = "hunterdata"
version = "0.1.0"
description = "Source-traceable Japan recruitment contact research workflow"
requires-python = ">=3.12,<3.13"
dependencies = []

[dependency-groups]
dev = [
    "pytest>=8.0",
]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

Run:

```bash
uv sync --python 3.12
uv run python --version
```

Expected: `uv run python --version` prints Python 3.12.x.

- [ ] **Step 3: Write `README.md`**

Create `README.md` with:

```markdown
# Japan Headhunter Contact Research

This project builds a local, source-traceable dataset of 100 Japan recruitment/headhunter contact records.

## Outputs

- `data/processed/japan_headhunters_contacts.csv`
- `data/processed/japan_headhunters_contacts.jsonl`
- `data/processed/japan_headhunters_sources.csv`
- `data/processed/qa_report.md`

## Requirements

- `uv`
- Python 3.12 managed by `uv`
- Dokobot CLI (`dokobot --help` should work)
- Chrome + Dokobot local bridge if using `dokobot read --local`

## Setup

```bash
uv sync --python 3.12
uv run python --version
```

## Source Rules

MHLW `人材サービス総合サイト` is the primary business verification source.
Company websites are the preferred source for email, phone, and contact form URLs.
JESRA and recognized certification directories are secondary verification sources.

Do not collect private social profiles, login-only data, paid database data, inferred email patterns, or personal non-business contact details.

## Run

```bash
uv run python -m scripts.collect_contacts --target-count 100 --use-dokobot-local
uv run python -m scripts.qa_report data/processed/japan_headhunters_contacts.csv
```
```

- [ ] **Step 4: Write `docs/research-protocol.md`**

Create `docs/research-protocol.md` with:

```markdown
# Research Protocol

## Objective

Collect 100 public business contact records for Japan recruitment/headhunter companies across all industries.

## Verification Hierarchy

1. `mhlw_verified`: listed in MHLW `人材サービス総合サイト` under `職業紹介事業`, preferably `有料職業紹介事業`.
2. `association_verified`: listed in JESRA or an official certification directory.
3. `business_keyword_verified`: official website contains recruitment/headhunting keywords.
4. `needs_manual_review`: not accepted into the final 100 until manually resolved.

## Business Keywords

- `人材紹介`
- `職業紹介`
- `転職エージェント`
- `採用支援`
- `ヘッドハンティング`
- `エグゼクティブサーチ`
- `サーチ`
- `スカウト`
- `executive search`
- `headhunting`
- `recruitment`
- `placement`

## Contact Source Rules

Accept:

- Company website contact pages.
- Public company profile pages.
- Public team pages where business email or phone is intentionally published.
- Official association/certification directories.

Reject:

- Login-only pages.
- Paid databases.
- Private LinkedIn/social profile pages.
- CAPTCHA bypasses.
- Inferred email patterns.
- Personal home or non-business contact details.

## Manual Review Notes

If a page requires browser interaction but remains publicly accessible, record:

- Timestamp.
- URL.
- Interaction summary.
- Visible evidence.
- Why it is acceptable.

Store these notes in `data/raw/manual_observations.md`.
```

- [ ] **Step 5: Write `data/interim/candidates.example.jsonl`**

Create `data/interim/candidates.example.jsonl` with one sample JSON line:

```json
{"company_name":"Sample Recruiting","company_url":"https://example.com","source_url":"https://example.com/contact","source_text_path":"data/raw/dokobot/20260521T030000Z-read-example-contact.txt","mhlw_text_path":"data/raw/dokobot/20260521T030010Z-read-mhlw-sample.txt","mhlw_source_url":"https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/","association_text_path":"","accessed_at":"2026-05-21T11:00:00+08:00"}
```

- [ ] **Step 6: Commit scaffold**

Run:

```bash
git add README.md .python-version pyproject.toml uv.lock docs/research-protocol.md data/raw/dokobot/.gitkeep data/interim/.gitkeep data/interim/candidates.example.jsonl data/processed/.gitkeep
git commit -m "docs: add contact research protocol"
```

Expected: commit succeeds. If the repository is not initialized, initialize it first with `git init` and then rerun the add/commit commands.

---

## Task 2: Schema, Normalization, And Writers

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/contact_schema.py`
- Create: `tests/test_contact_schema.py`

- [ ] **Step 1: Write failing schema tests**

Create `tests/test_contact_schema.py`:

```python
import csv
import json

from scripts.contact_schema import (
    FIELDNAMES,
    ContactRow,
    ValidationError,
    dedupe_key,
    normalize_phone,
    validate_row,
    write_outputs,
)


def valid_row(**overrides):
    row = {
        "record_id": "sample-co-https-example-com-contact",
        "company_name": "Sample Co",
        "contact_name": "",
        "title": "",
        "email": "info@example.com",
        "phone": "",
        "contact_form_url": "https://example.com/contact",
        "company_url": "https://example.com",
        "source_url": "https://example.com/contact",
        "mhlw_source_url": "https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/",
        "license_number": "13-ユ-000000",
        "license_type": "有料職業紹介事業",
        "city_or_prefecture": "Tokyo",
        "specialization": "All industries",
        "classification": "recruitment_agency",
        "evidence_keywords": "人材紹介;職業紹介",
        "verification_status": "mhlw_verified",
        "confidence": "high",
        "source_accessed_at": "2026-05-21T11:00:00+08:00",
        "notes": "",
    }
    row.update(overrides)
    return row


def test_field_order_is_stable():
    assert FIELDNAMES[:5] == [
        "record_id",
        "company_name",
        "contact_name",
        "title",
        "email",
    ]
    assert FIELDNAMES[-1] == "notes"
    assert len(FIELDNAMES) == 20


def test_validate_row_accepts_valid_company_contact():
    validate_row(valid_row())


def test_validate_row_requires_source_url():
    try:
        validate_row(valid_row(source_url=""))
    except ValidationError as exc:
        assert "source_url" in str(exc)
    else:
        raise AssertionError("Expected ValidationError")


def test_validate_row_requires_one_contact_channel():
    try:
        validate_row(valid_row(email="", phone="", contact_form_url=""))
    except ValidationError as exc:
        assert "contact channel" in str(exc)
    else:
        raise AssertionError("Expected ValidationError")


def test_normalize_phone_keeps_japan_number_digits_visible():
    assert normalize_phone("03-5253-1111") == "03-5253-1111"
    assert normalize_phone("+81 3 5253 1111") == "+81-3-5253-1111"


def test_dedupe_key_uses_company_and_contact_channels():
    key = dedupe_key(valid_row(company_name=" Sample Co. ", email="INFO@EXAMPLE.COM"))
    assert key == ("sample co.", "info@example.com", "", "https://example.com/contact")


def test_write_outputs_creates_matching_csv_and_jsonl(tmp_path):
    rows = [ContactRow(valid_row())]
    csv_path = tmp_path / "contacts.csv"
    jsonl_path = tmp_path / "contacts.jsonl"

    write_outputs(rows, csv_path, jsonl_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    with jsonl_path.open(encoding="utf-8") as handle:
        json_rows = [json.loads(line) for line in handle]

    assert csv_rows[0]["company_name"] == "Sample Co"
    assert json_rows[0]["company_name"] == "Sample Co"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run python -m pytest tests/test_contact_schema.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `scripts.contact_schema`.

- [ ] **Step 3: Implement schema module**

Create `scripts/__init__.py` as an empty file.

Create `scripts/contact_schema.py`:

```python
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FIELDNAMES = [
    "record_id",
    "company_name",
    "contact_name",
    "title",
    "email",
    "phone",
    "contact_form_url",
    "company_url",
    "source_url",
    "mhlw_source_url",
    "license_number",
    "license_type",
    "city_or_prefecture",
    "specialization",
    "classification",
    "evidence_keywords",
    "verification_status",
    "confidence",
    "source_accessed_at",
    "notes",
]

VALID_VERIFICATION_STATUSES = {
    "mhlw_verified",
    "association_verified",
    "business_keyword_verified",
    "needs_manual_review",
}
VALID_CONFIDENCE = {"high", "medium", "low"}


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ContactRow:
    values: dict[str, str]

    def as_dict(self) -> dict[str, str]:
        return {field: str(self.values.get(field, "")).strip() for field in FIELDNAMES}


def normalize_phone(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    value = re.sub(r"[()\s]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def normalize_url(value: str) -> str:
    return value.strip()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def dedupe_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("company_name", "").strip().lower(),
        normalize_email(row.get("email", "")),
        normalize_phone(row.get("phone", "")),
        normalize_url(row.get("contact_form_url", "")),
    )


def validate_row(row: dict[str, str]) -> None:
    missing_fields = [field for field in FIELDNAMES if field not in row]
    if missing_fields:
        raise ValidationError(f"missing fields: {', '.join(missing_fields)}")
    if not row["company_name"].strip():
        raise ValidationError("company_name is required")
    if not row["source_url"].strip():
        raise ValidationError("source_url is required")
    if not any(row[field].strip() for field in ("email", "phone", "contact_form_url")):
        raise ValidationError("at least one contact channel is required")
    if row["verification_status"] not in VALID_VERIFICATION_STATUSES:
        raise ValidationError(f"invalid verification_status: {row['verification_status']}")
    if row["confidence"] not in VALID_CONFIDENCE:
        raise ValidationError(f"invalid confidence: {row['confidence']}")
    if row["verification_status"] == "needs_manual_review":
        raise ValidationError("needs_manual_review rows cannot be accepted into final output")


def write_outputs(rows: Iterable[ContactRow], csv_path: Path, jsonl_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    materialized = [row.as_dict() for row in rows]
    for row in materialized:
        validate_row(row)

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(materialized)

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run schema tests**

Run:

```bash
uv run python -m pytest tests/test_contact_schema.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit schema**

Run:

```bash
git add scripts/__init__.py scripts/contact_schema.py tests/test_contact_schema.py
git commit -m "feat: add contact row schema"
```

---

## Task 3: Dokobot Client And Raw Capture

**Files:**
- Create: `scripts/dokobot_client.py`
- Create: `tests/test_dokobot_client.py`

- [ ] **Step 1: Write failing Dokobot client tests**

Create `tests/test_dokobot_client.py`:

```python
from pathlib import Path

from scripts.dokobot_client import DokobotClient, slugify


def test_slugify_keeps_safe_filename_chars():
    assert slugify("https://example.com/contact?x=1") == "https-example-com-contact-x-1"


def test_read_page_writes_raw_output(tmp_path):
    calls = []

    def fake_runner(args, timeout, text, capture_output, check):
        calls.append(args)

        class Result:
            stdout = "Company contact page text"
            stderr = ""

        return Result()

    client = DokobotClient(raw_dir=tmp_path, runner=fake_runner, local=True)
    output_path = client.read_page("https://example.com/contact")

    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == "Company contact page text"
    assert calls[0][:3] == ["dokobot", "read", "--local"]


def test_search_writes_raw_output(tmp_path):
    def fake_runner(args, timeout, text, capture_output, check):
        class Result:
            stdout = "Search result text"
            stderr = ""

        return Result()

    client = DokobotClient(raw_dir=tmp_path, runner=fake_runner, local=False)
    output_path = client.search("site:example.com 人材紹介", num=3)

    assert output_path.exists()
    assert "Search result text" in output_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run python -m pytest tests/test_dokobot_client.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `scripts.dokobot_client`.

- [ ] **Step 3: Implement Dokobot client**

Create `scripts/dokobot_client.py`:

```python
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:160] or "page"


class DokobotClient:
    def __init__(
        self,
        raw_dir: Path,
        runner: Runner = subprocess.run,
        local: bool = True,
        timeout_seconds: int = 90,
    ) -> None:
        self.raw_dir = raw_dir
        self.runner = runner
        self.local = local
        self.timeout_seconds = timeout_seconds

    def read_page(self, url: str) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = self.raw_dir / f"{timestamp}-read-{slugify(url)}.txt"
        args = ["dokobot", "read"]
        if self.local:
            args.append("--local")
        args.extend(["--timeout", str(self.timeout_seconds), url])
        return self._run_and_save(args, output_path)

    def search(self, query: str, num: int = 10) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = self.raw_dir / f"{timestamp}-search-{slugify(query)}.txt"
        args = ["dokobot", "search", "--num", str(num), query]
        return self._run_and_save(args, output_path)

    def _run_and_save(self, args: list[str], output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = self.runner(
            args,
            timeout=self.timeout_seconds + 15,
            text=True,
            capture_output=True,
            check=True,
        )
        output_path.write_text(result.stdout, encoding="utf-8")
        if result.stderr.strip():
            output_path.with_suffix(".stderr.txt").write_text(result.stderr, encoding="utf-8")
        return output_path
```

- [ ] **Step 4: Run Dokobot client tests**

Run:

```bash
uv run python -m pytest tests/test_dokobot_client.py -q
```

Expected: PASS.

- [ ] **Step 5: Verify Dokobot help commands manually**

Run:

```bash
dokobot --help
dokobot search --help
dokobot read --help
```

Expected: commands print usage. Do not run a target search in this step.

- [ ] **Step 6: Commit Dokobot client**

Run:

```bash
git add scripts/dokobot_client.py tests/test_dokobot_client.py
git commit -m "feat: add dokobot raw capture client"
```

---

## Task 4: Contact Extraction And Classification

**Files:**
- Create: `scripts/extract_contacts.py`
- Create: `tests/test_extract_contacts.py`

- [ ] **Step 1: Write failing extraction tests**

Create `tests/test_extract_contacts.py`:

```python
from scripts.extract_contacts import (
    classify_business,
    extract_contact_forms,
    extract_emails,
    extract_evidence_keywords,
    extract_phones,
)


def test_extract_emails_skips_obvious_image_filenames():
    text = "Contact info@example.co.jp but not logo@example.png"
    assert extract_emails(text) == ["info@example.co.jp"]


def test_extract_phones_finds_japan_business_phone():
    text = "電話番号 03-5253-1111 / FAX 03-0000-0000"
    assert "03-5253-1111" in extract_phones(text)


def test_extract_contact_forms_from_urls():
    text = "お問い合わせ https://example.co.jp/contact and https://example.co.jp/privacy"
    assert extract_contact_forms(text) == ["https://example.co.jp/contact"]


def test_extract_keywords_and_classification_for_executive_search():
    text = "当社はエグゼクティブサーチとヘッドハンティングを提供します。"
    keywords = extract_evidence_keywords(text)
    assert "エグゼクティブサーチ" in keywords
    assert classify_business(keywords) == "executive_search"


def test_classify_recruitment_agency():
    keywords = extract_evidence_keywords("人材紹介、職業紹介、転職エージェント")
    assert classify_business(keywords) == "recruitment_agency"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run python -m pytest tests/test_extract_contacts.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `scripts.extract_contacts`.

- [ ] **Step 3: Implement extraction module**

Create `scripts/extract_contacts.py`:

```python
from __future__ import annotations

import re
from urllib.parse import urlparse

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
ALL_KEYWORDS = EXECUTIVE_KEYWORDS + RECRUITMENT_KEYWORDS + STAFFING_KEYWORDS

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+81[-\s]?)?\d{1,4}[-\s]\d{1,4}[-\s]\d{3,4}")
URL_RE = re.compile(r"https?://[^\s<>\"]+")


def extract_emails(text: str) -> list[str]:
    emails = []
    for match in EMAIL_RE.findall(text):
        lowered = match.lower().rstrip(".,)")
        if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            continue
        if lowered not in emails:
            emails.append(lowered)
    return emails


def extract_phones(text: str) -> list[str]:
    phones = []
    for match in PHONE_RE.findall(text):
        normalized = re.sub(r"\s+", "-", match).strip(".,)")
        if normalized not in phones:
            phones.append(normalized)
    return phones


def extract_contact_forms(text: str) -> list[str]:
    forms = []
    for url in URL_RE.findall(text):
        clean = url.rstrip(".,)")
        path = urlparse(clean).path.lower()
        if any(token in path for token in ("contact", "inquiry", "toiawase", "otoiawase")):
            if clean not in forms:
                forms.append(clean)
    return forms


def extract_evidence_keywords(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for keyword in ALL_KEYWORDS:
        probe = keyword.lower()
        if probe in lowered and keyword not in found:
            found.append(keyword)
    return found


def classify_business(keywords: list[str]) -> str:
    lowered = {keyword.lower() for keyword in keywords}
    if any(keyword.lower() in lowered for keyword in EXECUTIVE_KEYWORDS):
        return "executive_search"
    if any(keyword.lower() in lowered for keyword in RECRUITMENT_KEYWORDS):
        return "recruitment_agency"
    if any(keyword.lower() in lowered for keyword in STAFFING_KEYWORDS):
        return "staffing_or_dispatch"
    return "mixed_hr_service"
```

- [ ] **Step 4: Run extraction tests**

Run:

```bash
uv run python -m pytest tests/test_extract_contacts.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit extraction module**

Run:

```bash
git add scripts/extract_contacts.py tests/test_extract_contacts.py
git commit -m "feat: extract recruitment contact evidence"
```

---

## Task 5: Source Verification Rules

**Files:**
- Create: `scripts/verify_sources.py`
- Create: `tests/test_verify_sources.py`

- [ ] **Step 1: Write failing verification tests**

Create `tests/test_verify_sources.py`:

```python
from scripts.verify_sources import (
    Verification,
    detect_license_number,
    verify_company,
)


def test_detect_license_number_for_paid_recruitment_license():
    assert detect_license_number("許可番号 13-ユ-123456") == "13-ユ-123456"


def test_verify_company_prefers_mhlw_verified():
    result = verify_company(
        mhlw_text="株式会社サンプル 13-ユ-123456 有料職業紹介事業",
        association_text="",
        business_text="人材紹介サービス",
    )
    assert result == Verification(
        status="mhlw_verified",
        confidence="high",
        license_number="13-ユ-123456",
        license_type="有料職業紹介事業",
    )


def test_verify_company_accepts_association_without_mhlw():
    result = verify_company(
        mhlw_text="",
        association_text="日本人材紹介事業協会 会員企業",
        business_text="人材紹介サービス",
    )
    assert result.status == "association_verified"
    assert result.confidence == "high"


def test_verify_company_accepts_business_keywords_as_medium():
    result = verify_company(mhlw_text="", association_text="", business_text="転職エージェント")
    assert result.status == "business_keyword_verified"
    assert result.confidence == "medium"


def test_verify_company_flags_missing_evidence():
    result = verify_company(mhlw_text="", association_text="", business_text="会社概要")
    assert result.status == "needs_manual_review"
    assert result.confidence == "low"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run python -m pytest tests/test_verify_sources.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `scripts.verify_sources`.

- [ ] **Step 3: Implement verification module**

Create `scripts/verify_sources.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.extract_contacts import extract_evidence_keywords


LICENSE_RE = re.compile(r"\b\d{2}[-－](?:ユ|ム|特|地)[-－]\d{6}\b")


@dataclass(frozen=True)
class Verification:
    status: str
    confidence: str
    license_number: str = ""
    license_type: str = ""


def detect_license_number(text: str) -> str:
    match = LICENSE_RE.search(text)
    if not match:
        return ""
    return match.group(0).replace("－", "-")


def detect_license_type(text: str) -> str:
    if "有料職業紹介事業" in text:
        return "有料職業紹介事業"
    if "無料職業紹介事業" in text:
        return "無料職業紹介事業"
    return ""


def verify_company(mhlw_text: str, association_text: str, business_text: str) -> Verification:
    license_number = detect_license_number(mhlw_text)
    license_type = detect_license_type(mhlw_text)
    if license_number or license_type:
        return Verification(
            status="mhlw_verified",
            confidence="high",
            license_number=license_number,
            license_type=license_type,
        )

    association_markers = ("日本人材紹介事業協会", "職業紹介優良事業者", "適正な有料職業紹介事業者")
    if any(marker in association_text for marker in association_markers):
        return Verification(status="association_verified", confidence="high")

    if extract_evidence_keywords(business_text):
        return Verification(status="business_keyword_verified", confidence="medium")

    return Verification(status="needs_manual_review", confidence="low")
```

- [ ] **Step 4: Run verification tests**

Run:

```bash
uv run python -m pytest tests/test_verify_sources.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit verification module**

Run:

```bash
git add scripts/verify_sources.py tests/test_verify_sources.py
git commit -m "feat: verify recruitment company evidence"
```

---

## Task 6: Collection Orchestrator

**Files:**
- Create: `scripts/collect_contacts.py`
- Create: `tests/test_collect_contacts.py`

- [ ] **Step 1: Write failing orchestrator tests**

Create `tests/test_collect_contacts.py`:

```python
import csv
import json

from pathlib import Path

from scripts.collect_contacts import build_record, load_candidate_records, write_accepted_records


def test_build_record_uses_contact_and_verification_evidence():
    record = build_record(
        company_name="Sample Recruiting",
        company_url="https://example.com",
        source_url="https://example.com/contact",
        source_text="お問い合わせ info@example.com 03-5253-1111 人材紹介",
        mhlw_text="Sample Recruiting 13-ユ-123456 有料職業紹介事業",
        mhlw_source_url="https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/",
        association_text="",
        accessed_at="2026-05-21T11:00:00+08:00",
    )

    assert record.values["email"] == "info@example.com"
    assert record.values["phone"] == "03-5253-1111"
    assert record.values["verification_status"] == "mhlw_verified"
    assert record.values["confidence"] == "high"
    assert record.values["classification"] == "recruitment_agency"


def test_write_accepted_records_dedupes_and_writes_outputs(tmp_path):
    record = build_record(
        company_name="Sample Recruiting",
        company_url="https://example.com",
        source_url="https://example.com/contact",
        source_text="お問い合わせ info@example.com 人材紹介",
        mhlw_text="13-ユ-123456 有料職業紹介事業",
        mhlw_source_url="https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/",
        association_text="",
        accessed_at="2026-05-21T11:00:00+08:00",
    )

    csv_path, jsonl_path, sources_path = write_accepted_records([record, record], tmp_path, target_count=1)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    with jsonl_path.open(encoding="utf-8") as handle:
        json_rows = [json.loads(line) for line in handle]

    assert len(rows) == 1
    assert len(json_rows) == 1
    assert sources_path.exists()


def test_load_candidate_records_builds_from_manifest(tmp_path):
    source_text = tmp_path / "source.txt"
    source_text.write_text("お問い合わせ info@example.com 人材紹介", encoding="utf-8")
    mhlw_text = tmp_path / "mhlw.txt"
    mhlw_text.write_text("13-ユ-123456 有料職業紹介事業", encoding="utf-8")
    manifest = tmp_path / "candidates.jsonl"
    manifest.write_text(
        '{"company_name":"Sample Recruiting","company_url":"https://example.com",'
        '"source_url":"https://example.com/contact",'
        f'"source_text_path":"{source_text}",'
        f'"mhlw_text_path":"{mhlw_text}",'
        '"mhlw_source_url":"https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/",'
        '"association_text_path":"","accessed_at":"2026-05-21T11:00:00+08:00"}\n',
        encoding="utf-8",
    )

    records = load_candidate_records(manifest)

    assert len(records) == 1
    assert records[0].values["verification_status"] == "mhlw_verified"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run python -m pytest tests/test_collect_contacts.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `scripts.collect_contacts`.

- [ ] **Step 3: Implement orchestrator core**

Create `scripts/collect_contacts.py`:

```python
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.contact_schema import ContactRow, dedupe_key, write_outputs
from scripts.dokobot_client import DokobotClient
from scripts.extract_contacts import (
    classify_business,
    extract_contact_forms,
    extract_emails,
    extract_evidence_keywords,
    extract_phones,
)
from scripts.verify_sources import verify_company


def stable_record_id(company_name: str, source_url: str) -> str:
    digest = hashlib.sha1(f"{company_name}|{source_url}".encode("utf-8")).hexdigest()[:10]
    safe_name = "".join(ch.lower() if ch.isalnum() else "-" for ch in company_name).strip("-")
    return f"{safe_name[:50]}-{digest}"


def build_record(
    *,
    company_name: str,
    company_url: str,
    source_url: str,
    source_text: str,
    mhlw_text: str,
    mhlw_source_url: str,
    association_text: str,
    accessed_at: str,
) -> ContactRow:
    emails = extract_emails(source_text)
    phones = extract_phones(source_text)
    contact_forms = extract_contact_forms(source_text)
    keywords = extract_evidence_keywords(source_text)
    verification = verify_company(mhlw_text, association_text, source_text)
    values = {
        "record_id": stable_record_id(company_name, source_url),
        "company_name": company_name,
        "contact_name": "",
        "title": "",
        "email": emails[0] if emails else "",
        "phone": phones[0] if phones else "",
        "contact_form_url": contact_forms[0] if contact_forms else "",
        "company_url": company_url,
        "source_url": source_url,
        "mhlw_source_url": mhlw_source_url,
        "license_number": verification.license_number,
        "license_type": verification.license_type,
        "city_or_prefecture": "",
        "specialization": "All industries",
        "classification": classify_business(keywords),
        "evidence_keywords": ";".join(keywords),
        "verification_status": verification.status,
        "confidence": verification.confidence,
        "source_accessed_at": accessed_at,
        "notes": "",
    }
    return ContactRow(values)


def read_text_or_empty(path_value: str) -> str:
    if not path_value:
        return ""
    return Path(path_value).read_text(encoding="utf-8")


def load_candidate_records(manifest_path: Path) -> list[ContactRow]:
    records = []
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            records.append(
                build_record(
                    company_name=item["company_name"],
                    company_url=item["company_url"],
                    source_url=item["source_url"],
                    source_text=read_text_or_empty(item["source_text_path"]),
                    mhlw_text=read_text_or_empty(item.get("mhlw_text_path", "")),
                    mhlw_source_url=item.get("mhlw_source_url", ""),
                    association_text=read_text_or_empty(item.get("association_text_path", "")),
                    accessed_at=item["accessed_at"],
                )
            )
    return records


def write_sources_csv(rows: list[dict[str, str]], sources_path: Path) -> None:
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    with sources_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["record_id", "company_name", "source_url", "mhlw_source_url", "verification_status"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "record_id": row["record_id"],
                    "company_name": row["company_name"],
                    "source_url": row["source_url"],
                    "mhlw_source_url": row["mhlw_source_url"],
                    "verification_status": row["verification_status"],
                }
            )


def write_accepted_records(
    records: list[ContactRow],
    output_dir: Path,
    target_count: int,
) -> tuple[Path, Path, Path]:
    accepted = []
    seen = set()
    for record in records:
        row = record.as_dict()
        key = dedupe_key(row)
        if key in seen:
            continue
        if row["verification_status"] == "needs_manual_review":
            continue
        if row["confidence"] == "low":
            continue
        seen.add(key)
        accepted.append(record)
        if len(accepted) >= target_count:
            break

    csv_path = output_dir / "japan_headhunters_contacts.csv"
    jsonl_path = output_dir / "japan_headhunters_contacts.jsonl"
    sources_path = output_dir / "japan_headhunters_sources.csv"
    write_outputs(accepted, csv_path, jsonl_path)
    write_sources_csv([record.as_dict() for record in accepted], sources_path)
    return csv_path, jsonl_path, sources_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-count", type=int, default=100)
    parser.add_argument("--use-dokobot-local", action="store_true")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/dokobot"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = DokobotClient(raw_dir=args.raw_dir, local=args.use_dokobot_local)
    candidate_path = Path("data/interim/candidates.jsonl")
    if not candidate_path.exists():
        print("Dokobot client ready:", client.raw_dir)
        print("Create data/interim/candidates.jsonl from captured official and company source reads.")
        return
    records = load_candidate_records(candidate_path)
    outputs = write_accepted_records(records, args.output_dir, target_count=args.target_count)
    print("Wrote:", ", ".join(str(path) for path in outputs))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run orchestrator tests**

Run:

```bash
uv run python -m pytest tests/test_collect_contacts.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit orchestrator core**

Run:

```bash
git add scripts/collect_contacts.py tests/test_collect_contacts.py
git commit -m "feat: assemble verified contact records"
```

---

## Task 7: QA Report

**Files:**
- Create: `scripts/qa_report.py`
- Create: `tests/test_qa_report.py`

- [ ] **Step 1: Write failing QA report tests**

Create `tests/test_qa_report.py`:

```python
import csv

from scripts.contact_schema import FIELDNAMES
from scripts.qa_report import build_report, load_rows


def test_build_report_counts_rows_and_statuses(tmp_path):
    csv_path = tmp_path / "contacts.csv"
    row = {field: "" for field in FIELDNAMES}
    row.update(
        {
            "record_id": "sample",
            "company_name": "Sample",
            "email": "info@example.com",
            "source_url": "https://example.com/contact",
            "verification_status": "mhlw_verified",
            "confidence": "high",
            "classification": "recruitment_agency",
        }
    )
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerow(row)

    rows = load_rows(csv_path)
    report = build_report(rows, expected_count=1)

    assert "Total accepted rows: 1" in report
    assert "mhlw_verified: 1" in report
    assert "Critical validation failures: 0" in report
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run python -m pytest tests/test_qa_report.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `scripts.qa_report`.

- [ ] **Step 3: Implement QA report module**

Create `scripts/qa_report.py`:

```python
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from scripts.contact_schema import FIELDNAMES, ValidationError, validate_row


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_report(rows: list[dict[str, str]], expected_count: int = 100) -> str:
    failures = []
    for index, row in enumerate(rows, start=2):
        try:
            validate_row(row)
        except ValidationError as exc:
            failures.append(f"CSV line {index}: {exc}")

    status_counts = Counter(row.get("verification_status", "") for row in rows)
    confidence_counts = Counter(row.get("confidence", "") for row in rows)
    classification_counts = Counter(row.get("classification", "") for row in rows)
    missing_counts = {
        field: sum(1 for row in rows if not row.get(field, "").strip())
        for field in FIELDNAMES
    }

    lines = [
        "# QA Report",
        "",
        f"Total accepted rows: {len(rows)}",
        f"Expected rows: {expected_count}",
        f"Critical validation failures: {len(failures)}",
        "",
        "## Verification Status",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(status_counts.items()))
    lines.extend(["", "## Confidence"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(confidence_counts.items()))
    lines.extend(["", "## Classification"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(classification_counts.items()))
    lines.extend(["", "## Missing Fields"])
    lines.extend(f"- {key}: {value}" for key, value in sorted(missing_counts.items()))
    lines.extend(["", "## Failures"])
    if failures:
        lines.extend(f"- {failure}" for failure in failures)
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("data/processed/qa_report.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(load_rows(args.csv_path), expected_count=args.expected_count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run QA report tests**

Run:

```bash
uv run python -m pytest tests/test_qa_report.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit QA report**

Run:

```bash
git add scripts/qa_report.py tests/test_qa_report.py
git commit -m "feat: add dataset QA report"
```

---

## Task 8: Data Collection Runbook For 100 Records

**Files:**
- Modify: `docs/research-protocol.md`
- Create: `data/raw/manual_observations.md`
- Create after execution: `data/processed/japan_headhunters_contacts.csv`
- Create after execution: `data/processed/japan_headhunters_contacts.jsonl`
- Create after execution: `data/processed/japan_headhunters_sources.csv`
- Create after execution: `data/processed/qa_report.md`

- [ ] **Step 1: Confirm Dokobot local readiness**

Run:

```bash
dokobot --help
dokobot read --help
dokobot search --help
```

Expected: all commands print usage. If local mode fails later, run:

```bash
dokobot install-bridge
```

- [ ] **Step 2: Gather official-source candidates**

Run these searches and save raw outputs through `DokobotClient.search()` or direct CLI with manual filename capture:

```bash
dokobot search --num 10 'site:jinzai.hellowork.mhlw.go.jp 職業紹介事業 有料職業紹介事業'
dokobot search --num 10 'site:jesra.or.jp 会員企業 人材紹介'
dokobot search --num 10 '職業紹介優良事業者 認定 企業一覧'
dokobot search --num 10 '医療 介護 保育 適正な有料職業紹介事業者 認定 事業者'
```

Expected: raw text saved under `data/raw/dokobot/`. Do not count a record unless a company can be connected to a public source URL.

- [ ] **Step 3: Verify MHLW business status for candidates**

Use the MHLW `人材サービス総合サイト`:

1. Open https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/
2. Choose `職業紹介事業`.
3. Search by company name or by region with `有料職業紹介事業`.
4. Record visible license evidence, including `許可・届出受理番号` when shown.
5. Capture the page text with Dokobot when possible:

```bash
dokobot read --local 'https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/' --timeout 90
```

If the result requires manual interaction, append a note to `data/raw/manual_observations.md`:

```markdown
## YYYY-MM-DDTHH:MM:SSZ MHLW verification

- Company:
- Visible license number:
- License type:
- Search URL:
- Interaction summary:
- Evidence copied from visible page:
```

- [ ] **Step 4: Read official company contact pages**

For each verified candidate, prefer pages named:

- `/contact`
- `/inquiry`
- `/company`
- `/about`
- `/recruitment`
- Japanese equivalents containing `お問い合わせ`, `会社概要`, `人材紹介`, or `職業紹介`

Run:

```bash
dokobot read --local '<company-contact-or-profile-url>' --timeout 90 -o 'data/raw/dokobot/<timestamp>-read-<company>.txt'
```

Expected: raw text contains at least one contact channel or a contact form URL.

- [ ] **Step 5: Build records from captured text**

Create `data/interim/candidates.jsonl` with one JSON object per candidate. Use the same shape as `data/interim/candidates.example.jsonl`:

```json
{"company_name":"Sample Recruiting","company_url":"https://example.com","source_url":"https://example.com/contact","source_text_path":"data/raw/dokobot/20260521T030000Z-read-example-contact.txt","mhlw_text_path":"data/raw/dokobot/20260521T030010Z-read-mhlw-sample.txt","mhlw_source_url":"https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/","association_text_path":"","accessed_at":"2026-05-21T11:00:00+08:00"}
```

For manually verified pages, write the copied visible text to a raw text file and reference that path in `mhlw_text_path` or `association_text_path`.

Expected for each accepted row:

- `verification_status` is not `needs_manual_review`.
- `confidence` is not `low`.
- `source_url` points to the page with the contact channel.
- `mhlw_source_url` is populated when MHLW verification was used.

- [ ] **Step 6: Write final outputs**

After accumulating at least 100 accepted candidate rows in `data/interim/candidates.jsonl`, run the writer:

```bash
uv run python -m scripts.collect_contacts --target-count 100 --use-dokobot-local
```

Expected:

- `data/processed/japan_headhunters_contacts.csv`
- `data/processed/japan_headhunters_contacts.jsonl`
- `data/processed/japan_headhunters_sources.csv`

- [ ] **Step 7: Generate QA report**

Run:

```bash
uv run python -m scripts.qa_report data/processed/japan_headhunters_contacts.csv --expected-count 100
```

Expected: `data/processed/qa_report.md` contains:

```text
Total accepted rows: 100
Expected rows: 100
Critical validation failures: 0
```

- [ ] **Step 8: Spot-check 10 random rows**

Run:

```bash
python - <<'PY'
import csv
import random

with open("data/processed/japan_headhunters_contacts.csv", newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

for row in random.sample(rows, min(10, len(rows))):
    print(row["company_name"], row["source_url"], row["verification_status"], row["confidence"])
PY
```

Expected: every printed source URL supports the contact channel and classification when opened/read.

- [ ] **Step 9: Commit final dataset and report**

Run:

```bash
git add data/raw data/interim data/processed docs/research-protocol.md
git commit -m "data: add verified japan recruitment contacts"
```

Expected: commit includes raw evidence, processed outputs, and QA report. If raw files are too large, commit processed outputs and QA report, then document raw file retention policy in `docs/research-protocol.md`.

---

## Self-Review Checklist

- Spec coverage: plan covers source hierarchy, MHLW verification, Dokobot raw capture, field schema, compliance boundaries, final outputs, and QA reporting.
- Placeholder scan: no red-flag placeholder instructions remain.
- Type consistency: `ContactRow`, `FIELDNAMES`, `Verification`, `build_record`, `write_accepted_records`, and `build_report` are defined before later tasks use them.
- Candidate loading: Task 6 defines `data/interim/candidates.jsonl` loading before Task 8 uses it.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-japan-headhunter-contact-research.md`.

Two execution options after `fw-plan-review`:

1. Subagent-Driven (recommended): dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution: execute tasks in this session using executing-plans, batch execution with checkpoints.

Recommended next gate: run `fw-plan-review` before implementation.
