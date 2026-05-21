from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Sequence
from urllib.parse import urlparse

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

Read the input JSONL, use its deterministic context as starting evidence, find the exact company's public business email or inquiry/contact form, and write exactly one JSON object per input job to the output JSONL.
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
        "notes": str(result.get("notes", "")).strip(),
    }
    for field in ("contact_form_url", "company_url", "source_url"):
        value = normalized[field]
        if value and not _is_http_url(value):
            raise ValueError(f"agent result for {record_id} has invalid {field}: {value}")
    if status in {"email_found", "contact_form_found", "official_site_found_no_contact"} and not normalized["source_url"]:
        raise ValueError(f"agent result for {record_id} must include source_url for status {status}")
    return normalized


def validate_agent_raw_evidence(
    result: dict[str, str],
    *,
    raw_dir: Path,
    require_raw: bool,
) -> None:
    if not require_raw or result["status"] == "error":
        return
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


def validate_agent_batches_complete(agent_dir: Path) -> None:
    batches = load_agent_batches(agent_dir)
    if not batches:
        return
    incomplete = [batch.batch_id for batch in batches if not agent_batch_complete(batch)]
    if incomplete:
        raise ValueError(f"Incomplete agent result batches: {', '.join(incomplete)}")


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


def agent_batch_complete(batch: AgentBatch) -> bool:
    expected = batch_expected_record_ids(batch)
    return expected <= batch_result_record_ids(batch)


def wait_for_agent_results(*, batches: list[AgentBatch], timeout_seconds: int, poll_seconds: float) -> None:
    if not batches:
        return
    deadline = time.monotonic() + timeout_seconds
    while True:
        incomplete = [batch.batch_id for batch in batches if not agent_batch_complete(batch)]
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
            validate_agent_batches_complete(args.agent_dir)
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
