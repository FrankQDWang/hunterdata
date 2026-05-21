import csv
import json
import subprocess

import pytest

from scripts.claude_agent_workflow import (
    AgentBatch,
    agent_batch_complete,
    build_agent_jobs,
    build_claude_background_command,
    default_candidate_provider,
    launch_claude_background_batches,
    merge_agent_results,
    parse_args,
    run_workflow,
    stream_static_enrichment_and_queue,
    validate_agent_batches_complete,
    wait_for_agent_results,
    write_agent_batches,
)
from scripts.enrich_contacts import ENRICHED_FIELDNAMES, SearchResult


def enriched_row(record_id="sample", email="", status="not_found"):
    row = {field: "" for field in ENRICHED_FIELDNAMES}
    row.update(
        {
            "record_id": record_id,
            "company_name": "株式会社 サンプル",
            "email": email,
            "phone": "03-1234-5678",
            "source_url": "https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/detail",
            "mhlw_source_url": "https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/detail",
            "license_number": "13-ユ-010101",
            "license_type": "有料職業紹介事業",
            "city_or_prefecture": "東京都",
            "classification": "recruitment_agency",
            "hunter_likelihood": "low",
            "hunter_likelihood_reason": "MHLW license only.",
            "evidence_keywords": "職業紹介",
            "verification_status": "mhlw_verified",
            "confidence": "high",
            "source_accessed_at": "2026-05-21T11:00:00+08:00",
            "enrichment_status": status,
        }
    )
    return row


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ENRICHED_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def test_build_agent_jobs_skips_rows_that_already_have_email():
    rows = [
        enriched_row("needs-agent"),
        enriched_row("has-email", email="info@sample.co.jp", status="email_found"),
    ]

    jobs = build_agent_jobs(
        rows,
        candidate_provider=lambda row: [SearchResult("公式", "https://sample.co.jp/", "株式会社 サンプル")],
    )

    assert [job.record_id for job in jobs] == ["needs-agent"]
    assert jobs[0].candidate_urls[0]["url"] == "https://sample.co.jp/"
    assert "Conservative static enrichment status" in jobs[0].deterministic_summary
    assert jobs[0].hunter_likelihood == "low"


def test_default_candidate_provider_skips_search_when_limit_is_zero(monkeypatch):
    def fail_search(company_name, phone):
        raise AssertionError("web search should not run")

    monkeypatch.setattr("scripts.claude_agent_workflow.web_search", fail_search)

    assert default_candidate_provider(enriched_row(), candidate_limit=0) == []


def test_write_agent_batches_creates_prompt_and_empty_result_files(tmp_path):
    jobs = build_agent_jobs([enriched_row("a"), enriched_row("b")], candidate_provider=lambda row: [])

    batches = write_agent_batches(
        jobs,
        agents=2,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=tmp_path / "raw",
    )

    assert len(batches) == 2
    assert "record_id" in batches[0].job_path.read_text(encoding="utf-8")
    prompt_text = batches[0].prompt_path.read_text(encoding="utf-8")
    assert "job envelope" in prompt_text
    assert str(batches[0].job_path) in prompt_text
    assert str(batches[0].result_path) in prompt_text
    assert batches[0].result_path.read_text(encoding="utf-8") == ""


def test_write_agent_batches_can_create_one_prompt_per_job(tmp_path):
    jobs = build_agent_jobs(
        [enriched_row("a"), enriched_row("b"), enriched_row("c")],
        candidate_provider=lambda row: [],
    )

    batches = write_agent_batches(
        jobs,
        agents=2,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=tmp_path / "raw",
        one_job_per_prompt=True,
    )

    assert len(batches) == 3
    assert [len(batch.job_path.read_text(encoding="utf-8").splitlines()) for batch in batches] == [1, 1, 1]


def test_build_claude_background_command_uses_bg_not_print_mode(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("process this batch", encoding="utf-8")

    command = build_claude_background_command(
        claude_bin="claude",
        prompt_path=prompt,
        agent="hunter-contact-enricher",
        model="",
        permission_mode="acceptEdits",
        tools="Read,Write,Bash",
        allowed_tools="Read,Write,Bash",
    )

    assert "--bg" in command
    assert "--agent" in command
    assert "hunter-contact-enricher" in command
    assert "-p" not in command
    assert "--output-format" not in command


def test_run_workflow_can_limit_agent_jobs_for_real_smoke_tests(tmp_path, monkeypatch):
    static_csv = tmp_path / "static.csv"
    write_csv(static_csv, [enriched_row("a"), enriched_row("b"), enriched_row("c")])
    monkeypatch.setattr(
        "scripts.claude_agent_workflow.build_agent_jobs",
        lambda rows, *, when, candidate_limit: [
            job
            for row in rows
            for job in build_agent_jobs([row], candidate_provider=lambda candidate_row: [])
        ],
    )
    args = parse_args(
        [
            "--static-csv",
            str(static_csv),
            "--agent-dir",
            str(tmp_path / "agents"),
            "--agent-raw-dir",
            str(tmp_path / "raw"),
            "--claude-mode",
            "prompt-files",
            "--max-agent-jobs",
            "1",
        ]
    )

    run_workflow(args)

    queue_lines = (tmp_path / "agents" / "agent_queue.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(queue_lines) == 1
    assert json.loads(queue_lines[0])["record_id"] == "a"


def test_stream_static_enrichment_queues_unresolved_rows_immediately(tmp_path, monkeypatch):
    base_csv = tmp_path / "base.csv"
    rows = [
        enriched_row("has-email", email="info@example.co.jp", status="email_found"),
        enriched_row("needs-agent", status="not_found"),
    ]
    rows[1]["email"] = ""
    write_csv(base_csv, rows)

    def fake_static_enrich_row(row, raw_dir):
        updated = dict(row)
        if row["record_id"] == "has-email":
            updated["email"] = "info@example.co.jp"
            updated["enrichment_status"] = "email_found"
        else:
            updated["email"] = ""
            updated["contact_form_url"] = ""
            updated["enrichment_status"] = "not_found"
        return updated

    monkeypatch.setattr("scripts.claude_agent_workflow.static_enrich_row", fake_static_enrich_row)
    args = parse_args(
        [
            "--base-csv",
            str(base_csv),
            "--static-csv",
            str(tmp_path / "static.csv"),
            "--static-zh-csv",
            str(tmp_path / "static_zh.csv"),
            "--static-raw-dir",
            str(tmp_path / "static_raw"),
            "--agent-dir",
            str(tmp_path / "agents"),
            "--agent-raw-dir",
            str(tmp_path / "raw"),
            "--stream-static-queue",
            "--candidate-limit",
            "0",
            "--static-sleep-seconds",
            "0",
        ]
    )

    stream_static_enrichment_and_queue(args)

    queue_lines = (tmp_path / "agents" / "agent_queue.jsonl").read_text(encoding="utf-8").splitlines()
    state = json.loads((tmp_path / "agents" / "stream_state.json").read_text(encoding="utf-8"))
    assert len(queue_lines) == 1
    queued = json.loads(queue_lines[0])
    assert queued["record_id"] == "needs-agent"
    assert queued["deterministic_summary"]
    assert "static_source_url" in queued
    assert (tmp_path / "agents" / "prompts" / "agent-001.md").exists()
    assert (tmp_path / "agents" / "results" / "agent-001-results.jsonl").read_text(encoding="utf-8") == ""
    assert state["done"] is True
    assert state["processed_rows"] == 2


def test_launch_claude_background_batches_logs_each_launch(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("process this batch", encoding="utf-8")
    batch = AgentBatch(
        batch_id="agent-001",
        job_path=tmp_path / "job.jsonl",
        prompt_path=prompt,
        result_path=tmp_path / "result.jsonl",
        log_path=tmp_path / "log.json",
    )
    commands = []

    def runner(command, *, cwd, capture_output, text, check):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="launched", stderr="")

    results = launch_claude_background_batches(
        [batch],
        claude_bin="claude",
        agent="hunter-contact-enricher",
        runner=runner,
        cwd=tmp_path,
    )

    assert results[0].batch_id == "agent-001"
    assert commands[0][1:4] == ["--bg", "--agent", "hunter-contact-enricher"]
    assert "-p" not in commands[0]
    assert "launched" in batch.log_path.read_text(encoding="utf-8")


def test_merge_agent_results_updates_by_record_id_and_preserves_order(tmp_path):
    base_csv = tmp_path / "base.csv"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    output_csv = tmp_path / "merged.csv"
    zh_output_csv = tmp_path / "merged_zh.csv"
    raw_dir = tmp_path / "raw"
    raw_file = raw_dir / "agent-002" / "second.txt"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_text("official contact page", encoding="utf-8")
    raw_file.with_name("second.txt.meta.json").write_text(
        json.dumps(
            {
                "tool": "dokobot",
                "mode": "local",
                "returncode": 0,
                "visible_tab": True,
                "command": ["dokobot", "read", "--local", "--device", "local-device", "--reuse-tab"],
            }
        ),
        encoding="utf-8",
    )
    rows = [enriched_row("first"), enriched_row("second")]
    write_csv(base_csv, rows)
    (result_dir / "agent-002-results.jsonl").write_text(
        json.dumps(
            {
                "record_id": "second",
                "company_name": "株式会社 サンプル",
                "email": "info@second.co.jp",
                "contact_form_url": "",
                "company_url": "https://second.co.jp/",
                "source_url": "https://second.co.jp/contact/",
                "source_text_path": str(raw_file),
                "status": "email_found",
                "confidence": "high",
                "hunter_likelihood": "high",
                "hunter_likelihood_reason": "Official site says Executive Search.",
                "notes": "official site",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    merged = merge_agent_results(
        base_csv=base_csv,
        result_dir=result_dir,
        raw_dir=raw_dir,
        output_csv=output_csv,
        zh_output_csv=zh_output_csv,
    )

    assert [row["record_id"] for row in merged] == ["first", "second"]
    assert merged[0]["email"] == ""
    assert merged[1]["email"] == "info@second.co.jp"
    assert merged[1]["hunter_likelihood"] == "high"
    assert merged[1]["hunter_likelihood_reason"] == "Official site says Executive Search."
    assert merged[1]["enrichment_status"] == "agent_email_found"
    assert zh_output_csv.exists()


def test_merge_agent_results_rejects_invalid_hunter_likelihood(tmp_path):
    base_csv = tmp_path / "base.csv"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    write_csv(base_csv, [enriched_row("known")])
    (result_dir / "agent-results.jsonl").write_text(
        json.dumps(
            {
                "record_id": "known",
                "status": "not_found",
                "confidence": "low",
                "hunter_likelihood": "maybe",
                "hunter_likelihood_reason": "unclear",
                "source_text_path": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hunter_likelihood"):
        merge_agent_results(
            base_csv=base_csv,
            result_dir=result_dir,
            raw_dir=tmp_path / "raw",
            output_csv=tmp_path / "out.csv",
            zh_output_csv=tmp_path / "out_zh.csv",
        )


def test_merge_agent_results_rejects_missing_raw_evidence(tmp_path):
    base_csv = tmp_path / "base.csv"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    write_csv(base_csv, [enriched_row("known")])
    (result_dir / "agent-results.jsonl").write_text(
        json.dumps(
            {
                "record_id": "known",
                "status": "not_found",
                "confidence": "low",
                "hunter_likelihood": "low",
                "hunter_likelihood_reason": "No official evidence found.",
                "source_text_path": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_text_path"):
        merge_agent_results(
            base_csv=base_csv,
            result_dir=result_dir,
            raw_dir=tmp_path / "raw",
            output_csv=tmp_path / "out.csv",
            zh_output_csv=tmp_path / "out_zh.csv",
        )


def test_merge_agent_results_rejects_unknown_record_id(tmp_path):
    base_csv = tmp_path / "base.csv"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    write_csv(base_csv, [enriched_row("known")])
    (result_dir / "agent-results.jsonl").write_text(
        json.dumps(
            {
                "record_id": "unknown",
                "status": "not_found",
                "confidence": "low",
                "hunter_likelihood": "low",
                "hunter_likelihood_reason": "No official evidence found.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown record_id"):
        merge_agent_results(
            base_csv=base_csv,
            result_dir=result_dir,
            output_csv=tmp_path / "out.csv",
            zh_output_csv=tmp_path / "out_zh.csv",
        )


def test_agent_batch_complete_requires_every_record_id(tmp_path):
    jobs = build_agent_jobs([enriched_row("a"), enriched_row("b")], candidate_provider=lambda row: [])
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=tmp_path / "raw",
    )[0]

    batch.result_path.write_text(json.dumps({"record_id": "a", "status": "not_found"}) + "\n", encoding="utf-8")

    assert not agent_batch_complete(batch)

    batch.result_path.write_text(
        json.dumps({"record_id": "a", "status": "not_found"}) + "\n"
        + json.dumps({"record_id": "b", "status": "not_found"}) + "\n",
        encoding="utf-8",
    )

    assert agent_batch_complete(batch)
    wait_for_agent_results(batches=[batch], timeout_seconds=1, poll_seconds=0.01)


def test_validate_agent_batches_complete_rejects_incomplete_results(tmp_path):
    jobs = build_agent_jobs([enriched_row("a")], candidate_provider=lambda row: [])
    write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=tmp_path / "raw",
    )

    with pytest.raises(ValueError, match="Incomplete agent result batches"):
        validate_agent_batches_complete(tmp_path)
