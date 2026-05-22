from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Sequence
from urllib.parse import urlparse

from scripts.contact_schema import VALID_HUNTER_LIKELIHOODS
from scripts.enrich_contacts import (
    ENRICHED_FIELDNAMES,
    SearchResult,
    rank_official_results,
    static_enrich_row,
    web_search,
    write_chinese_csv,
    write_enriched_csv,
)
from scripts.extract_contacts import extract_emails
from scripts.mhlw_collect import collect_from_mhlw
from scripts.qa_report import build_report, has_critical_failures
from scripts.enrich_contacts import enrich_contacts


AgentWhen = Literal["no_email", "no_email_or_form"]
Runner = Callable[..., subprocess.CompletedProcess[str]]

RESULT_STATUSES = {
    "email_found",
    "contact_form_found",
    "official_site_found_no_contact",
    "not_found",
    "error",
}
CONFIDENCE_VALUES = {"high", "medium", "low"}
STATUS_RANK = {
    "email_found": 4,
    "contact_form_found": 3,
    "official_site_found_no_contact": 2,
    "not_found": 1,
    "error": 0,
}
CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "": 0}
CORPORATE_TERMS = [
    "株式会社",
    "有限会社",
    "合同会社",
    "合資会社",
    "合名会社",
    "一般社団法人",
    "一般財団法人",
    "公益社団法人",
    "公益財団法人",
    "社会福祉法人",
    "医療法人",
    "学校法人",
    "宗教法人",
    "kabushikigaisha",
    "kabushiki",
    "kaisha",
    "k.k.",
    "kk",
    "co.,ltd.",
    "co.ltd.",
    "co ltd",
    "ltd.",
    "inc.",
    "corp.",
    "company",
]


@dataclass(frozen=True)
class AgentJob:
    record_id: str
    company_name: str
    phone: str
    contact_form_url: str
    company_url: str
    source_url: str
    mhlw_source_url: str
    license_number: str
    license_type: str
    city_or_prefecture: str
    hunter_likelihood: str
    hunter_likelihood_reason: str
    static_status: str
    static_source_url: str
    static_source_text_path: str
    static_notes: str
    deterministic_summary: str
    candidate_urls: list[dict[str, str]]


@dataclass(frozen=True)
class AgentBatch:
    batch_id: str
    job_path: Path
    prompt_path: Path
    result_path: Path
    log_path: Path


@dataclass(frozen=True)
class ClaudeRunResult:
    batch_id: str
    returncode: int
    command: list[str]
    log_path: Path


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def normalize_enriched_row(row: dict[str, str]) -> dict[str, str]:
    return {field: row.get(field, "") for field in ENRICHED_FIELDNAMES}


def should_agent_enrich(row: dict[str, str], *, when: AgentWhen) -> bool:
    has_email = bool(row.get("email", "").strip())
    has_form = bool(row.get("contact_form_url", "").strip())
    if when == "no_email":
        return not has_email
    if when == "no_email_or_form":
        return not has_email and not has_form
    raise ValueError(f"unsupported agent policy: {when}")


def default_candidate_provider(row: dict[str, str], *, candidate_limit: int = 5) -> list[SearchResult]:
    if candidate_limit <= 0:
        return []
    results = web_search(row.get("company_name", ""), row.get("phone", ""))
    ranked = rank_official_results(results, company_name=row.get("company_name", ""), phone=row.get("phone", ""))
    return ranked[:candidate_limit]


def build_agent_jobs(
    rows: list[dict[str, str]],
    *,
    when: AgentWhen = "no_email",
    candidate_limit: int = 5,
    candidate_provider: Callable[[dict[str, str]], list[SearchResult]] | None = None,
) -> list[AgentJob]:
    provider = candidate_provider or (lambda row: default_candidate_provider(row, candidate_limit=candidate_limit))
    jobs = []
    seen_record_ids: set[str] = set()
    for row in rows:
        normalized = normalize_enriched_row(row)
        record_id = normalized["record_id"]
        if record_id in seen_record_ids:
            raise ValueError(f"duplicate record_id values are not safe for agent backfill: {record_id}")
        seen_record_ids.add(record_id)
        if not should_agent_enrich(normalized, when=when):
            continue
        candidates = [
            {"title": result.title, "url": result.url, "description": result.description}
            for result in provider(normalized)
        ]
        jobs.append(
            AgentJob(
                record_id=record_id,
                company_name=normalized["company_name"],
                phone=normalized["phone"],
                contact_form_url=normalized["contact_form_url"],
                company_url=normalized["company_url"],
                source_url=normalized["source_url"],
                mhlw_source_url=normalized["mhlw_source_url"],
                license_number=normalized["license_number"],
                license_type=normalized["license_type"],
                city_or_prefecture=normalized["city_or_prefecture"],
                hunter_likelihood=normalized["hunter_likelihood"],
                hunter_likelihood_reason=normalized["hunter_likelihood_reason"],
                static_status=normalized["enrichment_status"],
                static_source_url=normalized["email_source_url"],
                static_source_text_path=normalized["email_source_text_path"],
                static_notes=normalized["notes"],
                deterministic_summary=build_deterministic_summary(normalized),
                candidate_urls=candidates,
            )
        )
    return jobs


def build_deterministic_summary(row: dict[str, str]) -> str:
    status = row.get("enrichment_status", "") or "unknown"
    parts = [f"Conservative static enrichment status: {status}."]
    if row.get("email"):
        parts.append(f"Static stage found email: {row['email']}.")
    if row.get("contact_form_url"):
        parts.append(f"Static stage found contact form: {row['contact_form_url']}.")
    if row.get("company_url"):
        parts.append(f"Static stage found likely official site: {row['company_url']}.")
    if row.get("email_source_url"):
        parts.append(f"Static evidence URL: {row['email_source_url']}.")
    if row.get("email_source_text_path"):
        parts.append(f"Static evidence raw path: {row['email_source_text_path']}.")
    if row.get("hunter_likelihood"):
        parts.append(
            f"Hunter likelihood: {row['hunter_likelihood']} ({row.get('hunter_likelihood_reason', '').strip()})."
        )
    if status == "not_found":
        parts.append("Static stage did not confidently confirm an official email, form, or site.")
    elif status == "official_site_found_no_contact":
        parts.append("Static stage found a likely official site but no public email/contact form.")
    elif status == "contact_form_found":
        parts.append("Static stage found a form but still no public email.")
    return " ".join(parts)


def write_agent_batches(
    jobs: list[AgentJob],
    *,
    agents: int,
    batch_dir: Path,
    prompt_dir: Path,
    result_dir: Path,
    log_dir: Path,
    raw_dir: Path,
    one_job_per_prompt: bool = False,
) -> list[AgentBatch]:
    for directory in (batch_dir, prompt_dir, result_dir, log_dir, raw_dir):
        directory.mkdir(parents=True, exist_ok=True)
    if not jobs:
        return []
    if one_job_per_prompt:
        batches = [[job] for job in jobs]
    else:
        agent_count = max(1, min(agents, len(jobs)))
        batches = [[] for _ in range(agent_count)]
        for index, job in enumerate(jobs):
            batches[index % agent_count].append(job)

    written = []
    for index, batch_jobs in enumerate(batches, start=1):
        batch_id = f"agent-{index:03d}"
        job_path = batch_dir / f"{batch_id}.jsonl"
        prompt_path = prompt_dir / f"{batch_id}.md"
        result_path = result_dir / f"{batch_id}-results.jsonl"
        log_path = log_dir / f"{batch_id}.json"
        job_path.write_text(
            "\n".join(json.dumps(asdict(job), ensure_ascii=False, sort_keys=False) for job in batch_jobs) + "\n",
            encoding="utf-8",
        )
        prompt_path.write_text(
            build_agent_prompt(
                batch_id=batch_id,
                job_path=job_path,
                result_path=result_path,
                raw_dir=raw_dir,
            ),
            encoding="utf-8",
        )
        result_path.write_text("", encoding="utf-8")
        written.append(AgentBatch(batch_id=batch_id, job_path=job_path, prompt_path=prompt_path, result_path=result_path, log_path=log_path))
    return written


def write_single_agent_batch(
    job: AgentJob,
    *,
    batch_index: int,
    batch_dir: Path,
    prompt_dir: Path,
    result_dir: Path,
    log_dir: Path,
    raw_dir: Path,
) -> AgentBatch:
    for directory in (batch_dir, prompt_dir, result_dir, log_dir, raw_dir):
        directory.mkdir(parents=True, exist_ok=True)
    batch_id = f"agent-{batch_index:03d}"
    job_path = batch_dir / f"{batch_id}.jsonl"
    prompt_path = prompt_dir / f"{batch_id}.md"
    result_path = result_dir / f"{batch_id}-results.jsonl"
    log_path = log_dir / f"{batch_id}.json"
    job_path.write_text(json.dumps(asdict(job), ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    prompt_path.write_text(
        build_agent_prompt(
            batch_id=batch_id,
            job_path=job_path,
            result_path=result_path,
            raw_dir=raw_dir,
        ),
        encoding="utf-8",
    )
    result_path.write_text("", encoding="utf-8")
    return AgentBatch(batch_id=batch_id, job_path=job_path, prompt_path=prompt_path, result_path=result_path, log_path=log_path)


def build_agent_prompt(*, batch_id: str, job_path: Path, result_path: Path, raw_dir: Path) -> str:
    return f"""Process this hunter-contact-enricher job.

The agent definition contains the task rules, acceptance criteria, Dokobot requirement, and output schema. This file is only the job envelope.

Input JSONL:
`{job_path}`

Output JSONL:
`{result_path}`

Raw Dokobot evidence directory:
`{raw_dir}/{batch_id}/`

Read the input JSONL, use its deterministic context as starting evidence, find the exact company's public business email or inquiry/contact form, judge `hunter_likelihood`, and write exactly one JSON object per input job to the output JSONL.
"""


def validate_agent_result(result: dict[str, Any], known_record_ids: set[str]) -> dict[str, str]:
    record_id = str(result.get("record_id", "")).strip()
    if record_id not in known_record_ids:
        raise ValueError(f"agent result has unknown record_id: {record_id}")
    status = str(result.get("status", "")).strip()
    if status not in RESULT_STATUSES:
        raise ValueError(f"agent result for {record_id} has invalid status: {status}")
    confidence = str(result.get("confidence", "")).strip() or "low"
    if confidence not in CONFIDENCE_VALUES:
        raise ValueError(f"agent result for {record_id} has invalid confidence: {confidence}")
    hunter_likelihood = str(result.get("hunter_likelihood", "")).strip()
    if hunter_likelihood not in VALID_HUNTER_LIKELIHOODS:
        raise ValueError(f"agent result for {record_id} has invalid hunter_likelihood: {hunter_likelihood}")

    email = str(result.get("email", "")).strip().lower()
    if email and extract_emails(email) != [email]:
        raise ValueError(f"agent result for {record_id} has invalid email: {email}")

    normalized = {
        "record_id": record_id,
        "company_name": str(result.get("company_name", "")).strip(),
        "email": email,
        "contact_form_url": str(result.get("contact_form_url", "")).strip(),
        "company_url": str(result.get("company_url", "")).strip(),
        "source_url": str(result.get("source_url", "")).strip(),
        "source_text_path": str(result.get("source_text_path", "")).strip(),
        "status": status,
        "confidence": confidence,
        "hunter_likelihood": hunter_likelihood,
        "hunter_likelihood_reason": str(result.get("hunter_likelihood_reason", "")).strip(),
        "notes": str(result.get("notes", "")).strip(),
    }
    for field in ("contact_form_url", "company_url", "source_url"):
        value = normalized[field]
        if value and not _is_http_url(value):
            raise ValueError(f"agent result for {record_id} has invalid {field}: {value}")
    _validate_agent_status_fields(normalized)
    if status in {"email_found", "contact_form_found", "official_site_found_no_contact"} and not normalized["source_url"]:
        raise ValueError(f"agent result for {record_id} must include source_url for status {status}")
    return normalized


def _validate_agent_status_fields(result: dict[str, str]) -> None:
    record_id = result["record_id"]
    status = result["status"]
    email = result["email"]
    contact_form_url = result["contact_form_url"]
    company_url = result["company_url"]

    if status == "email_found" and not email:
        raise ValueError(f"agent result for {record_id} has status email_found but no email")
    if status == "contact_form_found":
        if not contact_form_url:
            raise ValueError(f"agent result for {record_id} has status contact_form_found but no contact_form_url")
        if email:
            raise ValueError(f"agent result for {record_id} has status contact_form_found but also includes email")
    if status == "official_site_found_no_contact":
        if email or contact_form_url:
            raise ValueError(
                f"agent result for {record_id} has status official_site_found_no_contact but includes email/contact_form_url"
            )
        if not company_url:
            raise ValueError(f"agent result for {record_id} has status official_site_found_no_contact but no company_url")
    if status == "not_found" and (email or contact_form_url):
        raise ValueError(f"agent result for {record_id} has status not_found but includes email/contact_form_url")


def validate_agent_raw_evidence(
    result: dict[str, str],
    *,
    raw_dir: Path,
    require_raw: bool,
) -> Path | None:
    if not require_raw or result["status"] == "error":
        return None
    source_text_path = result["source_text_path"]
    if not source_text_path:
        raise ValueError(f"agent result for {result['record_id']} must include source_text_path")
    path = Path(source_text_path)
    raw_root = raw_dir.resolve()
    resolved = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
    try:
        resolved.relative_to(raw_root)
    except ValueError as exc:
        raise ValueError(
            f"agent result for {result['record_id']} has source_text_path outside {raw_dir}: {source_text_path}"
        ) from exc
    if not resolved.exists():
        raise ValueError(f"agent result for {result['record_id']} source_text_path does not exist: {source_text_path}")
    if resolved.stat().st_size == 0:
        raise ValueError(f"agent result for {result['record_id']} source_text_path is empty: {source_text_path}")
    meta_path = resolved.with_name(f"{resolved.name}.meta.json")
    if not meta_path.exists():
        raise ValueError(f"agent result for {result['record_id']} is missing Dokobot metadata: {meta_path}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"agent result for {result['record_id']} has invalid Dokobot metadata: {meta_path}") from exc
    command = meta.get("command") if isinstance(meta.get("command"), list) else []
    if meta.get("tool") != "dokobot" or meta.get("mode") != "local" or meta.get("returncode") != 0:
        raise ValueError(f"agent result for {result['record_id']} metadata does not prove successful local Dokobot read: {meta_path}")
    if "--local" not in command or "--device" not in command or "--reuse-tab" not in command:
        raise ValueError(f"agent result for {result['record_id']} metadata does not prove local Dokobot reuse-tab use: {meta_path}")
    return resolved


def validate_agent_semantic_evidence(result: dict[str, str], job: dict[str, Any], raw_path: Path | None) -> None:
    if raw_path is None:
        return
    raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
    meta_path = raw_path.with_name(f"{raw_path.name}.meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    evidence_url = str(meta.get("url") or "")
    result_source_url = str(result.get("source_url", ""))
    if result_source_url and evidence_url and not _same_site_url(result_source_url, evidence_url):
        raise ValueError(
            f"agent result for {result['record_id']} source_url does not match Dokobot metadata URL: "
            f"{result_source_url} vs {evidence_url}"
        )

    signals = []
    if _raw_text_contains_company(raw_text, str(job.get("company_name", ""))):
        signals.append("company_name")
    if _digits(job.get("phone", "")) and _digits(job.get("phone", "")) in _digits(raw_text):
        signals.append("phone")
    if _license_matches(raw_text, str(job.get("license_number", ""))):
        signals.append("license_number")
    if _same_site_url(evidence_url, str(job.get("company_url", ""))):
        signals.append("company_url_domain")

    if not signals:
        raise ValueError(
            f"agent result for {result['record_id']} does not prove same company in raw evidence: "
            "expected company name, phone, license number, or known company domain"
        )


def _raw_text_contains_company(raw_text: str, company_name: str) -> bool:
    tokens = _company_identity_tokens(company_name)
    haystack = _compact_identity_text(raw_text)
    return any(token in haystack for token in tokens)


def _company_identity_tokens(company_name: str) -> list[str]:
    compact = _compact_identity_text(company_name)
    for term in CORPORATE_TERMS:
        compact = compact.replace(_compact_identity_text(term), " ")
    tokens = [token for token in re.split(r"[^0-9a-zぁ-んァ-ン一-龥ー]+", compact) if _useful_identity_token(token)]
    squashed = re.sub(r"\s+", "", compact)
    if _useful_identity_token(squashed):
        tokens.append(squashed)
    return sorted(set(tokens), key=len, reverse=True)


def _compact_identity_text(value: str) -> str:
    return re.sub(r"\s+", "", value.casefold().replace("　", " "))


def _useful_identity_token(token: str) -> bool:
    if not token:
        return False
    if token in {_compact_identity_text(term) for term in CORPORATE_TERMS}:
        return False
    return len(token) >= 2 if re.search(r"[ぁ-んァ-ン一-龥ー]", token) else len(token) >= 3


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def _license_matches(raw_text: str, license_number: str) -> bool:
    if not license_number:
        return False
    return _compact_identity_text(license_number) in _compact_identity_text(raw_text)


def _same_site_url(left: str, right: str) -> bool:
    left_host = urlparse(left).netloc.casefold()
    right_host = urlparse(right).netloc.casefold()
    if not left_host or not right_host:
        return False
    return left_host == right_host or left_host.endswith(f".{right_host}") or right_host.endswith(f".{left_host}")


def validate_agent_batch_result(
    batch: AgentBatch,
    *,
    raw_dir: Path,
    require_raw: bool = True,
) -> None:
    expected_jobs = batch_jobs_by_record_id(batch)
    expected = set(expected_jobs)
    if not expected:
        return
    if not batch.result_path.exists():
        raise ValueError(f"{batch.batch_id} result file does not exist: {batch.result_path}")

    seen: set[str] = set()
    errors: list[str] = []
    for line_number, line in enumerate(batch.result_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            result = validate_agent_result(json.loads(line), expected)
            if result["status"] == "error":
                raise ValueError(f"agent result for {result['record_id']} returned error status")
            raw_path = validate_agent_raw_evidence(result, raw_dir=raw_dir, require_raw=require_raw)
            validate_agent_semantic_evidence(result, expected_jobs[result["record_id"]], raw_path)
        except Exception as exc:
            errors.append(f"{batch.result_path}:{line_number}: {exc}")
            continue
        seen.add(result["record_id"])

    missing = expected - seen
    if missing:
        errors.append(f"{batch.batch_id} missing expected record_id values: {', '.join(sorted(missing))}")
    if errors:
        raise ValueError("; ".join(errors))


def batch_jobs_by_record_id(batch: AgentBatch) -> dict[str, dict[str, Any]]:
    jobs = {}
    for line in batch.job_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        record_id = str(item.get("record_id", "")).strip()
        if record_id:
            jobs[record_id] = item
    return jobs


def record_agent_validation_failure(
    *,
    agent_dir: Path,
    batch_id: str,
    failure_reason: str,
    max_attempts: int = 3,
) -> dict[str, Any]:
    batches = {batch.batch_id: batch for batch in load_agent_batches(agent_dir)}
    batch = batches.get(batch_id)
    if batch is None:
        raise ValueError(f"unknown agent batch: {batch_id}")

    state_path = agent_dir / "retry_state.json"
    state = _load_retry_state(state_path)
    entry = dict(state.get(batch_id, {}))
    attempts = int(entry.get("attempts") or 0) + 1
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    archived_result = _archive_agent_result(batch, attempts, agent_dir=agent_dir)
    if attempts >= max_attempts:
        entry.update(
            {
                "status": "quarantined",
                "attempts": attempts,
                "max_attempts": max_attempts,
                "last_error": failure_reason,
                "updated_at": now,
                "archived_result": archived_result,
            }
        )
        state[batch_id] = entry
        _write_retry_state(state_path, state)
        _append_quarantine(agent_dir, batch=batch, entry=entry)
        return {"status": "quarantined", "batch_id": batch_id, **entry}

    retry_prompt_path = _write_retry_prompt(
        agent_dir=agent_dir,
        batch=batch,
        next_attempt=attempts + 1,
        failure_reason=failure_reason,
    )
    entry.update(
        {
            "status": "retry",
            "attempts": attempts,
            "max_attempts": max_attempts,
            "last_error": failure_reason,
            "updated_at": now,
            "archived_result": archived_result,
            "retry_prompt_path": str(retry_prompt_path),
        }
    )
    state[batch_id] = entry
    _write_retry_state(state_path, state)
    return {"status": "retry", "batch_id": batch_id, **entry}


def quarantined_batch_ids(agent_dir: Path) -> set[str]:
    state = _load_retry_state(agent_dir / "retry_state.json")
    return {batch_id for batch_id, entry in state.items() if entry.get("status") == "quarantined"}


def _load_retry_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_retry_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _archive_agent_result(batch: AgentBatch, attempts: int, *, agent_dir: Path) -> str:
    failed_dir = agent_dir / "failed_results"
    failed_dir.mkdir(parents=True, exist_ok=True)
    archive_path = failed_dir / f"{batch.batch_id}-attempt-{attempts:03d}-results.jsonl"
    if batch.result_path.exists() and batch.result_path.stat().st_size > 0:
        batch.result_path.replace(archive_path)
    else:
        archive_path.write_text("", encoding="utf-8")
    batch.result_path.parent.mkdir(parents=True, exist_ok=True)
    batch.result_path.write_text("", encoding="utf-8")
    return str(archive_path)


def _write_retry_prompt(
    *,
    agent_dir: Path,
    batch: AgentBatch,
    next_attempt: int,
    failure_reason: str,
) -> Path:
    retry_dir = agent_dir / "retry_prompts"
    retry_dir.mkdir(parents=True, exist_ok=True)
    retry_prompt_path = retry_dir / f"{batch.batch_id}-attempt-{next_attempt:03d}.md"
    original_prompt = batch.prompt_path.read_text(encoding="utf-8") if batch.prompt_path.exists() else ""
    retry_prompt_path.write_text(
        "\n".join(
            [
                "Retry this hunter-contact-enricher job.",
                "",
                f"Previous validation failure: {failure_reason}",
                "",
                "Do not repeat the failed evidence path unless it clearly proves the exact company.",
                "Write the corrected JSONL to the same output path required by the original prompt.",
                "",
                original_prompt,
            ]
        ),
        encoding="utf-8",
    )
    return retry_prompt_path


def _append_quarantine(agent_dir: Path, *, batch: AgentBatch, entry: dict[str, Any]) -> None:
    path = agent_dir / "quarantine.jsonl"
    record = {
        "batch_id": batch.batch_id,
        "job_path": str(batch.job_path),
        "result_path": str(batch.result_path),
        **entry,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")


def stream_static_enrichment_and_queue(args: argparse.Namespace) -> None:
    with args.base_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    queue_path = args.agent_dir / "agent_queue.jsonl"
    state_path = args.agent_dir / "stream_state.json"
    batch_dir = args.agent_dir / "batches"
    prompt_dir = args.agent_dir / "prompts"
    result_dir = args.agent_dir / "results"
    log_dir = args.agent_dir / "logs"
    for directory in (args.agent_dir, batch_dir, prompt_dir, result_dir, log_dir, args.agent_raw_dir):
        directory.mkdir(parents=True, exist_ok=True)
    queue_path.write_text("", encoding="utf-8")

    enriched_rows: list[dict[str, str]] = []
    agent_jobs = 0
    total = len(rows)
    for row_number, row in enumerate(rows, start=1):
        updated = static_enrich_row(row, args.static_raw_dir)
        enriched_rows.append(updated)
        write_enriched_csv(enriched_rows, args.static_csv)
        write_chinese_csv(enriched_rows, args.static_zh_csv)

        ready_job = None
        if not args.max_agent_jobs or agent_jobs < args.max_agent_jobs:
            jobs = build_agent_jobs([updated], when=args.agent_when, candidate_limit=args.candidate_limit)
            ready_job = jobs[0] if jobs else None
        if ready_job:
            agent_jobs += 1
            batch = write_single_agent_batch(
                ready_job,
                batch_index=agent_jobs,
                batch_dir=batch_dir,
                prompt_dir=prompt_dir,
                result_dir=result_dir,
                log_dir=log_dir,
                raw_dir=args.agent_raw_dir,
            )
            with queue_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(ready_job), ensure_ascii=False, sort_keys=False) + "\n")
            print(
                f"AGENT_JOB_READY {batch.batch_id} {ready_job.record_id} {ready_job.company_name}",
                file=sys.stderr,
                flush=True,
            )

        state_path.write_text(
            json.dumps(
                {
                    "processed_rows": row_number,
                    "total_rows": total,
                    "agent_jobs": agent_jobs,
                    "done": False,
                    "updated_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"[{row_number}/{total}] {row.get('company_name', '')}: {updated.get('enrichment_status', '')}",
            file=sys.stderr,
            flush=True,
        )
        if args.static_sleep_seconds:
            time.sleep(args.static_sleep_seconds)

    state_path.write_text(
        json.dumps(
            {
                "processed_rows": len(enriched_rows),
                "total_rows": total,
                "agent_jobs": agent_jobs,
                "done": True,
                "updated_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Streaming static enrichment complete. Agent jobs: {agent_jobs}", file=sys.stderr, flush=True)


def merge_agent_results(
    *,
    base_csv: Path,
    result_dir: Path,
    output_csv: Path,
    zh_output_csv: Path,
    raw_dir: Path = Path("data/raw/claude_agents"),
    strict: bool = True,
    require_raw: bool = True,
) -> list[dict[str, str]]:
    rows = [normalize_enriched_row(row) for row in load_csv(base_csv)]
    known_record_ids = {row["record_id"] for row in rows}
    best_results: dict[str, dict[str, str]] = {}
    errors = []
    for path in sorted(result_dir.glob("*.jsonl")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                result = validate_agent_result(json.loads(line), known_record_ids)
                validate_agent_raw_evidence(result, raw_dir=raw_dir, require_raw=require_raw)
            except Exception as exc:
                message = f"{path}:{line_number}: {exc}"
                if strict:
                    raise ValueError(message) from exc
                errors.append(message)
                continue
            previous = best_results.get(result["record_id"])
            if previous is None or _result_score(result) > _result_score(previous):
                best_results[result["record_id"]] = result

    merged = []
    for row in rows:
        result = best_results.get(row["record_id"])
        if result:
            if result["email"]:
                row["email"] = result["email"]
            if result["contact_form_url"]:
                row["contact_form_url"] = result["contact_form_url"]
            if result["company_url"]:
                row["company_url"] = result["company_url"]
            if result["source_url"]:
                row["email_source_url"] = result["source_url"]
                row["email_source_text_path"] = result["source_text_path"]
            row["confidence"] = result["confidence"]
            row["hunter_likelihood"] = result["hunter_likelihood"]
            row["hunter_likelihood_reason"] = result["hunter_likelihood_reason"]
            row["enrichment_status"] = f"agent_{result['status']}"
            agent_note = f"Agent contact enrichment: {result['notes']}".strip()
            row["notes"] = "; ".join(part for part in (row.get("notes", ""), agent_note) if part)
        merged.append(row)

    write_enriched_csv(merged, output_csv)
    write_chinese_csv(merged, zh_output_csv)
    if errors:
        (output_csv.parent / "agent_merge_warnings.txt").write_text("\n".join(errors) + "\n", encoding="utf-8")
    return merged


def load_agent_batches(agent_dir: Path) -> list[AgentBatch]:
    batch_dir = agent_dir / "batches"
    result_dir = agent_dir / "results"
    prompt_dir = agent_dir / "prompts"
    log_dir = agent_dir / "logs"
    batches = []
    for job_path in sorted(batch_dir.glob("*.jsonl")):
        batch_id = job_path.stem
        batches.append(
            AgentBatch(
                batch_id=batch_id,
                job_path=job_path,
                prompt_path=prompt_dir / f"{batch_id}.md",
                result_path=result_dir / f"{batch_id}-results.jsonl",
                log_path=log_dir / f"{batch_id}.json",
            )
        )
    return batches


def validate_agent_batches_complete(
    agent_dir: Path,
    *,
    raw_dir: Path = Path("data/raw/claude_agents"),
    require_raw: bool = True,
) -> None:
    batches = load_agent_batches(agent_dir)
    if not batches:
        return
    quarantined = quarantined_batch_ids(agent_dir)
    incomplete = []
    for batch in batches:
        if batch.batch_id in quarantined:
            continue
        try:
            validate_agent_batch_result(batch, raw_dir=raw_dir, require_raw=require_raw)
        except ValueError as exc:
            incomplete.append(f"{batch.batch_id} ({exc})")
    if incomplete:
        raise ValueError(f"Incomplete agent result batches: {'; '.join(incomplete)}")


def agent_batch_statuses(
    agent_dir: Path,
    *,
    raw_dir: Path = Path("data/raw/claude_agents"),
    require_raw: bool = True,
) -> list[dict[str, str]]:
    batches = load_agent_batches(agent_dir)
    retry_state = _load_retry_state(agent_dir / "retry_state.json")
    quarantined = {
        batch_id
        for batch_id, entry in retry_state.items()
        if isinstance(entry, dict) and entry.get("status") == "quarantined"
    }
    statuses = []
    for batch in batches:
        item = {
            "batch_id": batch.batch_id,
            "job_path": str(batch.job_path),
            "result_path": str(batch.result_path),
            "status": "incomplete",
        }
        if batch.batch_id in quarantined:
            item["status"] = "quarantined"
            last_error = retry_state.get(batch.batch_id, {}).get("last_error", "")
            if last_error:
                item["reason"] = str(last_error)
            statuses.append(item)
            continue
        try:
            validate_agent_batch_result(batch, raw_dir=raw_dir, require_raw=require_raw)
        except ValueError as exc:
            item["reason"] = str(exc)
        else:
            item["status"] = "valid"
        statuses.append(item)
    return statuses


def batch_expected_record_ids(batch: AgentBatch) -> set[str]:
    return {
        json.loads(line)["record_id"]
        for line in batch.job_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def batch_result_record_ids(batch: AgentBatch) -> set[str]:
    if not batch.result_path.exists():
        return set()
    record_ids = set()
    for line in batch.result_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record_ids.add(json.loads(line).get("record_id", ""))
        except json.JSONDecodeError:
            continue
    return {record_id for record_id in record_ids if record_id}


def agent_batch_complete(
    batch: AgentBatch,
    *,
    raw_dir: Path | None = None,
    require_raw: bool = True,
) -> bool:
    if raw_dir is None:
        expected = batch_expected_record_ids(batch)
        return expected <= batch_result_record_ids(batch)
    try:
        validate_agent_batch_result(batch, raw_dir=raw_dir, require_raw=require_raw)
    except ValueError:
        return False
    return True


def wait_for_agent_results(
    *,
    batches: list[AgentBatch],
    timeout_seconds: int,
    poll_seconds: float,
    raw_dir: Path | None = None,
    require_raw: bool = True,
) -> None:
    if not batches:
        return
    deadline = time.monotonic() + timeout_seconds
    while True:
        incomplete = [
            batch.batch_id
            for batch in batches
            if not agent_batch_complete(batch, raw_dir=raw_dir, require_raw=require_raw)
        ]
        if not incomplete:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for agent result batches: {', '.join(incomplete)}")
        print(f"Waiting for Claude agent batches: {', '.join(incomplete)}", file=sys.stderr, flush=True)
        time.sleep(poll_seconds)


def _result_score(result: dict[str, str]) -> tuple[int, int]:
    return STATUS_RANK[result["status"]], CONFIDENCE_RANK[result["confidence"]]


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def build_claude_background_command(
    *,
    claude_bin: str,
    prompt_path: Path,
    agent: str,
    model: str,
    permission_mode: str,
    tools: str,
    allowed_tools: str,
    chrome: bool = False,
) -> list[str]:
    prompt = prompt_path.read_text(encoding="utf-8")
    command = [claude_bin]
    if chrome:
        command.append("--chrome")
    command.extend(["--bg", "--agent", agent, "--permission-mode", permission_mode])
    if model:
        command.extend(["--model", model])
    if tools:
        command.extend(["--tools", tools])
    if allowed_tools:
        command.extend(["--allowedTools", allowed_tools])
    command.append(prompt)
    return command


def launch_claude_background_batches(
    batches: list[AgentBatch],
    *,
    claude_bin: str = "claude",
    agent: str = "hunter-contact-enricher",
    agents: int = 5,
    model: str = "",
    permission_mode: str = "acceptEdits",
    tools: str = "Read,Write,Bash",
    allowed_tools: str = "Read,Write,Bash",
    chrome: bool = False,
    runner: Runner = subprocess.run,
    cwd: Path = Path("."),
) -> list[ClaudeRunResult]:
    if not batches:
        return []

    def run_one(batch: AgentBatch) -> ClaudeRunResult:
        command = build_claude_background_command(
            claude_bin=claude_bin,
            prompt_path=batch.prompt_path,
            agent=agent,
            model=model,
            permission_mode=permission_mode,
            tools=tools,
            allowed_tools=allowed_tools,
            chrome=chrome,
        )
        completed = runner(command, cwd=str(cwd), capture_output=True, text=True, check=False)
        batch.log_path.write_text(
            json.dumps(
                {
                    "batch_id": batch.batch_id,
                    "returncode": completed.returncode,
                    "command": command[:-1] + ["<prompt omitted>"],
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return ClaudeRunResult(batch.batch_id, completed.returncode, command, batch.log_path)

    results = []
    with ThreadPoolExecutor(max_workers=max(1, agents)) as executor:
        futures = [executor.submit(run_one, batch) for batch in batches]
        for future in as_completed(futures):
            results.append(future.result())
    failures = [result for result in results if result.returncode != 0]
    if failures:
        failed = ", ".join(f"{result.batch_id} rc={result.returncode}" for result in failures)
        raise RuntimeError(f"Claude background agent launch failures: {failed}")
    return sorted(results, key=lambda result: result.batch_id)


def run_workflow(args: argparse.Namespace) -> None:
    base_csv = args.base_csv
    if args.refresh_mhlw or not base_csv.exists():
        if not args.legacy_prototype_workflow:
            raise SystemExit(
                "Legacy prototype workflow is disabled by default. Use Claude Code /hunter-contact-backfill, "
                "or pass --legacy-prototype-workflow for the old japan_headhunters_* flow."
            )
        collect_from_mhlw(
            target_count=args.target_count,
            raw_dir=args.mhlw_raw_dir,
            output_dir=args.output_dir,
            sleep_seconds=args.mhlw_sleep_seconds,
        )

    if args.refresh_static or not args.static_csv.exists():
        enrich_contacts(
            input_csv=base_csv,
            output_csv=args.static_csv,
            zh_output_csv=args.static_zh_csv,
            raw_dir=args.static_raw_dir,
            sleep_seconds=args.static_sleep_seconds,
        )

    rows = load_csv(args.static_csv)
    jobs = build_agent_jobs(rows, when=args.agent_when, candidate_limit=args.candidate_limit)
    if args.max_agent_jobs:
        jobs = jobs[: args.max_agent_jobs]
    queue_path = args.agent_dir / "agent_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        "\n".join(json.dumps(asdict(job), ensure_ascii=False, sort_keys=False) for job in jobs) + ("\n" if jobs else ""),
        encoding="utf-8",
    )
    batches = write_agent_batches(
        jobs,
        agents=args.agents,
        batch_dir=args.agent_dir / "batches",
        prompt_dir=args.agent_dir / "prompts",
        result_dir=args.agent_dir / "results",
        log_dir=args.agent_dir / "logs",
        raw_dir=args.agent_raw_dir,
        one_job_per_prompt=args.one_job_per_prompt,
    )
    print(f"Agent jobs: {len(jobs)}")
    print(f"Agent batches: {len(batches)}")
    print(f"Agent queue: {queue_path}")

    if args.claude_mode == "background":
        launch_claude_background_batches(
            batches,
            claude_bin=args.claude_bin,
            agent=args.claude_agent,
            agents=args.agents,
            model=args.model,
            permission_mode=args.permission_mode,
            tools=args.tools,
            allowed_tools=args.allowed_tools,
            chrome=args.claude_chrome,
            cwd=Path.cwd(),
        )
        print("Claude background agents launched.")
        if not args.wait:
            print("After they finish writing result JSONL files, rerun with --merge-only.")
            return
        wait_for_agent_results(
            batches=batches,
            raw_dir=args.agent_raw_dir,
            require_raw=not args.allow_missing_agent_raw,
            timeout_seconds=args.wait_timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
    else:
        print("Prompt files generated. Run them with Claude Code or Claude Desktop, then rerun with --merge-only.")
        for batch in batches:
            print(batch.prompt_path)
        return

    merged = merge_agent_results(
        base_csv=args.static_csv,
        result_dir=args.agent_dir / "results",
        raw_dir=args.agent_raw_dir,
        output_csv=args.final_csv,
        zh_output_csv=args.final_zh_csv,
        strict=not args.non_strict_merge,
        require_raw=not args.allow_missing_agent_raw,
    )
    report = build_report(merged, expected_count=args.target_count)
    args.final_qa_path.write_text(report, encoding="utf-8")
    if has_critical_failures(report):
        raise SystemExit(1)
    print(args.final_csv)
    print(args.final_zh_csv)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MHLW + deterministic static enrichment + Claude agent backfill.")
    parser.add_argument("--target-count", type=int, default=100)
    parser.add_argument("--agents", type=int, default=5)
    parser.add_argument("--agent-when", choices=["no_email", "no_email_or_form"], default="no_email")
    parser.add_argument("--candidate-limit", type=int, default=5)
    parser.add_argument("--max-agent-jobs", type=int, default=0)
    parser.add_argument("--one-job-per-prompt", action="store_true")
    parser.add_argument("--claude-mode", choices=["prompt-files", "background"], default="prompt-files")
    parser.add_argument("--mhlw-only", action="store_true")
    parser.add_argument("--stream-static-queue", action="store_true")
    parser.add_argument("--merge-only", action="store_true")
    parser.add_argument("--validate-agent-results", action="store_true")
    parser.add_argument("--validate-agent-batch", default="")
    parser.add_argument("--agent-status", action="store_true")
    parser.add_argument("--record-agent-failure", default="")
    parser.add_argument("--failure-reason", default="")
    parser.add_argument("--max-agent-attempts", type=int, default=3)
    parser.add_argument("--legacy-prototype-workflow", action="store_true")
    parser.add_argument("--refresh-mhlw", action="store_true")
    parser.add_argument("--refresh-static", action="store_true")
    parser.add_argument("--non-strict-merge", action="store_true")
    parser.add_argument("--allow-missing-agent-raw", action="store_true")
    parser.add_argument("--allow-incomplete-agent-results", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--wait-timeout-seconds", type=int, default=7200)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--claude-agent", default="hunter-contact-enricher")
    parser.add_argument("--model", default="")
    parser.add_argument("--permission-mode", default="acceptEdits")
    parser.add_argument("--tools", default="Read,Write,Bash")
    parser.add_argument("--allowed-tools", default="Read,Write,Bash")
    parser.add_argument("--claude-chrome", action="store_true")
    parser.add_argument("--mhlw-sleep-seconds", type=float, default=0.1)
    parser.add_argument("--static-sleep-seconds", type=float, default=0.1)
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--base-csv", type=Path, default=Path("data/processed/japan_headhunters_contacts.csv"))
    parser.add_argument("--static-csv", type=Path, default=Path("data/processed/japan_headhunters_contacts_static_enriched.csv"))
    parser.add_argument("--static-zh-csv", type=Path, default=Path("data/processed/japan_headhunters_contacts_static_enriched_zh.csv"))
    parser.add_argument("--final-csv", type=Path, default=Path("data/processed/japan_headhunters_contacts_agent_enriched.csv"))
    parser.add_argument("--final-zh-csv", type=Path, default=Path("data/processed/japan_headhunters_contacts_agent_enriched_zh.csv"))
    parser.add_argument("--final-qa-path", type=Path, default=Path("data/processed/qa_report_agent_enriched.md"))
    parser.add_argument("--mhlw-raw-dir", type=Path, default=Path("data/raw/mhlw"))
    parser.add_argument("--static-raw-dir", type=Path, default=Path("data/raw/static_enrichment"))
    parser.add_argument("--agent-dir", type=Path, default=Path("data/interim/claude_agents"))
    parser.add_argument("--agent-raw-dir", type=Path, default=Path("data/raw/claude_agents"))
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    if args.record_agent_failure:
        outcome = record_agent_validation_failure(
            agent_dir=args.agent_dir,
            batch_id=args.record_agent_failure,
            failure_reason=args.failure_reason or "agent validation failed",
            max_attempts=args.max_agent_attempts,
        )
        print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
        return
    if args.validate_agent_batch:
        batches = {batch.batch_id: batch for batch in load_agent_batches(args.agent_dir)}
        batch = batches.get(args.validate_agent_batch)
        if batch is None:
            raise SystemExit(f"unknown agent batch: {args.validate_agent_batch}")
        validate_agent_batch_result(
            batch,
            raw_dir=args.agent_raw_dir,
            require_raw=not args.allow_missing_agent_raw,
        )
        print(f"agent_batch_valid {args.validate_agent_batch}")
        return
    if args.validate_agent_results:
        validate_agent_batches_complete(
            args.agent_dir,
            raw_dir=args.agent_raw_dir,
            require_raw=not args.allow_missing_agent_raw,
        )
        print("agent_results_valid")
        return
    if args.agent_status:
        print(
            json.dumps(
                {
                    "batches": agent_batch_statuses(
                        args.agent_dir,
                        raw_dir=args.agent_raw_dir,
                        require_raw=not args.allow_missing_agent_raw,
                    )
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    if args.mhlw_only:
        collect_from_mhlw(
            target_count=args.target_count,
            raw_dir=args.mhlw_raw_dir,
            output_dir=args.output_dir,
            sleep_seconds=args.mhlw_sleep_seconds,
        )
        return
    if args.stream_static_queue:
        stream_static_enrichment_and_queue(args)
        return
    if args.merge_only:
        if not args.allow_incomplete_agent_results:
            validate_agent_batches_complete(
                args.agent_dir,
                raw_dir=args.agent_raw_dir,
                require_raw=not args.allow_missing_agent_raw,
            )
        merged = merge_agent_results(
            base_csv=args.static_csv,
            result_dir=args.agent_dir / "results",
            raw_dir=args.agent_raw_dir,
            output_csv=args.final_csv,
            zh_output_csv=args.final_zh_csv,
            strict=not args.non_strict_merge,
            require_raw=not args.allow_missing_agent_raw,
        )
        report = build_report(merged, expected_count=args.target_count)
        args.final_qa_path.write_text(report, encoding="utf-8")
        print(args.final_csv)
        print(args.final_zh_csv)
        if has_critical_failures(report):
            raise SystemExit(1)
        return
    run_workflow(args)


if __name__ == "__main__":
    main()
