import csv
import json
import subprocess

import pytest

from scripts.claude_agent_workflow import (
    AgentBatch,
    agent_batch_complete,
    agent_batch_statuses,
    batch_expected_record_ids,
    build_agent_jobs,
    build_claude_background_command,
    default_candidate_provider,
    launch_claude_background_batches,
    merge_agent_results,
    parse_args,
    record_agent_validation_failure,
    run_workflow,
    stream_static_enrichment_and_queue,
    validate_agent_batch_result,
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
    base_csv = tmp_path / "base.csv"
    static_csv = tmp_path / "static.csv"
    rows = [enriched_row("a"), enriched_row("b"), enriched_row("c")]
    write_csv(base_csv, rows)
    write_csv(static_csv, rows)
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
            "--base-csv",
            str(base_csv),
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


def test_run_workflow_blocks_legacy_prototype_defaults_without_explicit_flag(tmp_path):
    args = parse_args(
        [
            "--base-csv",
            str(tmp_path / "missing.csv"),
            "--static-csv",
            str(tmp_path / "missing-static.csv"),
            "--claude-mode",
            "prompt-files",
        ]
    )

    with pytest.raises(SystemExit, match="Legacy prototype workflow is disabled"):
        run_workflow(args)


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


def test_merge_agent_results_uses_agent_confidence_for_adopted_result(tmp_path):
    base_csv = tmp_path / "base.csv"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_file = _write_raw(raw_dir, "agent-001", "a.txt")
    write_csv(base_csv, [enriched_row("a")])
    (result_dir / "agent-001-results.jsonl").write_text(
        json.dumps(
            {
                "record_id": "a",
                "company_name": "株式会社 サンプル",
                "email": "info@sample.co.jp",
                "contact_form_url": "",
                "company_url": "https://sample.co.jp/",
                "source_url": "https://sample.co.jp/contact/",
                "source_text_path": str(raw_file),
                "status": "email_found",
                "confidence": "low",
                "hunter_likelihood": "medium",
                "hunter_likelihood_reason": "Official site describes recruitment service.",
                "notes": "low confidence official contact evidence",
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
        output_csv=tmp_path / "out.csv",
        zh_output_csv=tmp_path / "out_zh.csv",
    )

    assert merged[0]["confidence"] == "low"


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


def test_merge_agent_results_rejects_official_site_no_contact_with_contact_form(tmp_path):
    base_csv = tmp_path / "base.csv"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_file = _write_raw(raw_dir, "agent-001", "known.txt")
    write_csv(base_csv, [enriched_row("known")])
    result = _agent_result("known", raw_file)
    result["status"] = "official_site_found_no_contact"
    result["contact_form_url"] = "https://sample.co.jp/contact/"
    (result_dir / "agent-results.jsonl").write_text(
        json.dumps(result, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="official_site_found_no_contact"):
        merge_agent_results(
            base_csv=base_csv,
            result_dir=result_dir,
            raw_dir=raw_dir,
            output_csv=tmp_path / "out.csv",
            zh_output_csv=tmp_path / "out_zh.csv",
        )


def test_merge_agent_results_rejects_not_found_with_contact_form(tmp_path):
    base_csv = tmp_path / "base.csv"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_file = _write_raw(raw_dir, "agent-001", "known.txt")
    write_csv(base_csv, [enriched_row("known")])
    result = _agent_result("known", raw_file)
    result["status"] = "not_found"
    result["source_url"] = ""
    result["contact_form_url"] = "https://sample.co.jp/contact/"
    (result_dir / "agent-results.jsonl").write_text(
        json.dumps(result, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not_found"):
        merge_agent_results(
            base_csv=base_csv,
            result_dir=result_dir,
            raw_dir=raw_dir,
            output_csv=tmp_path / "out.csv",
            zh_output_csv=tmp_path / "out_zh.csv",
        )


def test_merge_agent_results_rejects_contact_form_status_without_contact_form(tmp_path):
    base_csv = tmp_path / "base.csv"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_file = _write_raw(raw_dir, "agent-001", "known.txt")
    write_csv(base_csv, [enriched_row("known")])
    result = _agent_result("known", raw_file)
    result["status"] = "contact_form_found"
    result["contact_form_url"] = ""
    (result_dir / "agent-results.jsonl").write_text(
        json.dumps(result, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="contact_form_found"):
        merge_agent_results(
            base_csv=base_csv,
            result_dir=result_dir,
            raw_dir=raw_dir,
            output_csv=tmp_path / "out.csv",
            zh_output_csv=tmp_path / "out_zh.csv",
        )


def test_merge_agent_results_rejects_email_status_without_email(tmp_path):
    base_csv = tmp_path / "base.csv"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    raw_dir = tmp_path / "raw"
    raw_file = _write_raw(raw_dir, "agent-001", "known.txt")
    write_csv(base_csv, [enriched_row("known")])
    result = _agent_result("known", raw_file)
    result["status"] = "email_found"
    result["email"] = ""
    (result_dir / "agent-results.jsonl").write_text(
        json.dumps(result, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="email_found"):
        merge_agent_results(
            base_csv=base_csv,
            result_dir=result_dir,
            raw_dir=raw_dir,
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
    raw_dir = tmp_path / "raw"
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=raw_dir,
    )[0]

    batch.result_path.write_text(json.dumps(_agent_result("a", _write_raw(raw_dir, "agent-001", "a.txt"))) + "\n", encoding="utf-8")

    assert not agent_batch_complete(batch, raw_dir=raw_dir)

    batch.result_path.write_text(
        json.dumps(_agent_result("a", _write_raw(raw_dir, "agent-001", "a.txt"))) + "\n"
        + json.dumps(_agent_result("b", _write_raw(raw_dir, "agent-001", "b.txt"))) + "\n",
        encoding="utf-8",
    )

    assert agent_batch_complete(batch, raw_dir=raw_dir)
    validate_agent_batch_result(batch, raw_dir=raw_dir)
    wait_for_agent_results(batches=[batch], raw_dir=raw_dir, timeout_seconds=1, poll_seconds=0.01)


def test_agent_batch_complete_accepts_not_found_with_same_company_evidence(tmp_path):
    jobs = build_agent_jobs([enriched_row("a")], candidate_provider=lambda row: [])
    raw_dir = tmp_path / "raw"
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=raw_dir,
    )[0]
    raw_file = _write_raw(raw_dir, "agent-001", "a.txt")
    result = _agent_result("a", raw_file)
    result["status"] = "not_found"
    result["source_url"] = ""
    batch.result_path.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")

    assert agent_batch_complete(batch, raw_dir=raw_dir)
    validate_agent_batch_result(batch, raw_dir=raw_dir)


def test_agent_batch_complete_rejects_invalid_schema_even_when_record_id_is_present(tmp_path):
    jobs = build_agent_jobs([enriched_row("a")], candidate_provider=lambda row: [])
    raw_dir = tmp_path / "raw"
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=raw_dir,
    )[0]

    batch.result_path.write_text(json.dumps({"record_id": "a", "status": "not_found"}) + "\n", encoding="utf-8")

    assert not agent_batch_complete(batch, raw_dir=raw_dir)
    with pytest.raises(ValueError, match="hunter_likelihood"):
        validate_agent_batch_result(batch, raw_dir=raw_dir)


def test_agent_batch_complete_rejects_wrong_company_evidence(tmp_path):
    jobs = build_agent_jobs([enriched_row("a")], candidate_provider=lambda row: [])
    raw_dir = tmp_path / "raw"
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=raw_dir,
    )[0]
    raw_file = _write_raw(
        raw_dir,
        "agent-001",
        "a.txt",
        text="Wrong Company official page 06-9999-9999",
        url="https://wrong.example/contact/",
    )
    result = _agent_result("a", raw_file)
    result["company_url"] = "https://wrong.example/"
    result["source_url"] = "https://wrong.example/contact/"
    batch.result_path.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")

    assert not agent_batch_complete(batch, raw_dir=raw_dir)
    with pytest.raises(ValueError, match="does not prove same company"):
        validate_agent_batch_result(batch, raw_dir=raw_dir)


def test_agent_batch_complete_rejects_self_reported_url_as_company_evidence(tmp_path):
    row = enriched_row("a")
    row["company_url"] = "https://sample.co.jp/"
    jobs = build_agent_jobs([row], candidate_provider=lambda row: [])
    raw_dir = tmp_path / "raw"
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=raw_dir,
    )[0]
    raw_file = _write_raw(
        raw_dir,
        "agent-001",
        "a.txt",
        text="Wrong Company official page 06-9999-9999",
        url="https://wrong.example/contact/",
    )
    result = _agent_result("a", raw_file)
    result["company_url"] = "https://sample.co.jp/"
    result["source_url"] = "https://wrong.example/contact/"
    batch.result_path.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")

    assert not agent_batch_complete(batch, raw_dir=raw_dir)
    with pytest.raises(ValueError, match="does not prove same company"):
        validate_agent_batch_result(batch, raw_dir=raw_dir)


def test_agent_batch_complete_rejects_source_url_that_does_not_match_raw_metadata(tmp_path):
    row = enriched_row("a")
    row["company_url"] = "https://sample.co.jp/"
    jobs = build_agent_jobs([row], candidate_provider=lambda row: [])
    raw_dir = tmp_path / "raw"
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=raw_dir,
    )[0]
    raw_file = _write_raw(raw_dir, "agent-001", "a.txt", url="https://sample.co.jp/contact/")
    result = _agent_result("a", raw_file)
    result["source_url"] = "https://wrong.example/contact/"
    batch.result_path.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")

    assert not agent_batch_complete(batch, raw_dir=raw_dir)
    with pytest.raises(ValueError, match="source_url does not match Dokobot metadata URL"):
        validate_agent_batch_result(batch, raw_dir=raw_dir)


def test_agent_batch_complete_rejects_error_status_for_retry(tmp_path):
    jobs = build_agent_jobs([enriched_row("a")], candidate_provider=lambda row: [])
    raw_dir = tmp_path / "raw"
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "batches",
        prompt_dir=tmp_path / "prompts",
        result_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        raw_dir=raw_dir,
    )[0]
    batch.result_path.write_text(
        json.dumps(
            {
                "record_id": "a",
                "status": "error",
                "confidence": "low",
                "hunter_likelihood": "low",
                "hunter_likelihood_reason": "Tool failed.",
                "notes": "dokobot timeout",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert not agent_batch_complete(batch, raw_dir=raw_dir)
    with pytest.raises(ValueError, match="returned error status"):
        validate_agent_batch_result(batch, raw_dir=raw_dir)


def test_record_agent_validation_failure_archives_bad_result_and_creates_retry_prompt(tmp_path):
    jobs = build_agent_jobs([enriched_row("a")], candidate_provider=lambda row: [])
    raw_dir = tmp_path / "raw"
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "agents" / "batches",
        prompt_dir=tmp_path / "agents" / "prompts",
        result_dir=tmp_path / "agents" / "results",
        log_dir=tmp_path / "agents" / "logs",
        raw_dir=raw_dir,
    )[0]
    batch.result_path.write_text("bad json\n", encoding="utf-8")

    outcome = record_agent_validation_failure(
        agent_dir=tmp_path / "agents",
        batch_id="agent-001",
        failure_reason="invalid json",
        max_attempts=3,
    )

    assert outcome["status"] == "retry"
    assert outcome["attempts"] == 1
    assert batch.result_path.read_text(encoding="utf-8") == ""
    assert (tmp_path / "agents" / "failed_results" / "agent-001-attempt-001-results.jsonl").exists()
    retry_prompt = tmp_path / "agents" / "retry_prompts" / "agent-001-attempt-002.md"
    assert retry_prompt.exists()
    assert "invalid json" in retry_prompt.read_text(encoding="utf-8")


def test_quarantined_agent_batch_counts_complete_and_preserves_static_row(tmp_path):
    jobs = build_agent_jobs([enriched_row("a")], candidate_provider=lambda row: [])
    raw_dir = tmp_path / "raw"
    batch = write_agent_batches(
        jobs,
        agents=1,
        batch_dir=tmp_path / "agents" / "batches",
        prompt_dir=tmp_path / "agents" / "prompts",
        result_dir=tmp_path / "agents" / "results",
        log_dir=tmp_path / "agents" / "logs",
        raw_dir=raw_dir,
    )[0]
    batch.result_path.write_text("bad json\n", encoding="utf-8")

    outcome = record_agent_validation_failure(
        agent_dir=tmp_path / "agents",
        batch_id="agent-001",
        failure_reason="semantic mismatch",
        max_attempts=1,
    )

    assert outcome["status"] == "quarantined"
    assert (tmp_path / "agents" / "quarantine.jsonl").exists()
    assert batch.result_path.read_text(encoding="utf-8") == ""
    validate_agent_batches_complete(tmp_path / "agents", raw_dir=raw_dir)


def test_agent_batch_statuses_reports_valid_quarantined_and_incomplete(tmp_path):
    raw_dir = tmp_path / "raw"
    jobs = build_agent_jobs(
        [enriched_row("valid"), enriched_row("quarantined"), enriched_row("incomplete")],
        candidate_provider=lambda row: [],
    )
    batches = write_agent_batches(
        jobs,
        agents=3,
        batch_dir=tmp_path / "agents" / "batches",
        prompt_dir=tmp_path / "agents" / "prompts",
        result_dir=tmp_path / "agents" / "results",
        log_dir=tmp_path / "agents" / "logs",
        raw_dir=raw_dir,
        one_job_per_prompt=True,
    )
    batches_by_job = {next(iter(batch_expected_record_ids(batch))): batch for batch in batches}
    batches_by_job["valid"].result_path.write_text(
        json.dumps(_agent_result("valid", _write_raw(raw_dir, "agent-001", "valid.txt")), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    batches_by_job["quarantined"].result_path.write_text("bad json\n", encoding="utf-8")
    record_agent_validation_failure(
        agent_dir=tmp_path / "agents",
        batch_id=batches_by_job["quarantined"].batch_id,
        failure_reason="invalid json",
        max_attempts=1,
    )

    statuses = {
        item["batch_id"]: item["status"]
        for item in agent_batch_statuses(tmp_path / "agents", raw_dir=raw_dir)
    }

    assert statuses[batches_by_job["valid"].batch_id] == "valid"
    assert statuses[batches_by_job["quarantined"].batch_id] == "quarantined"
    assert statuses[batches_by_job["incomplete"].batch_id] == "incomplete"


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
        validate_agent_batches_complete(tmp_path, raw_dir=tmp_path / "raw")


def _write_raw(
    raw_dir,
    batch_id,
    name,
    *,
    text="株式会社 サンプル official evidence 03-1234-5678 13-ユ-010101",
    url="https://sample.co.jp/",
):
    raw_file = raw_dir / batch_id / name
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(text, encoding="utf-8")
    raw_file.with_name(f"{raw_file.name}.meta.json").write_text(
        json.dumps(
            {
                "tool": "dokobot",
                "mode": "local",
                "returncode": 0,
                "url": url,
                "command": ["dokobot", "read", "--local", "--device", "local-device", "--reuse-tab"],
            }
        ),
        encoding="utf-8",
    )
    return raw_file


def _agent_result(record_id, raw_file):
    return {
        "record_id": record_id,
        "company_name": "株式会社 サンプル",
        "email": "",
        "contact_form_url": "",
        "company_url": "https://sample.co.jp/",
        "source_url": "https://sample.co.jp/",
        "source_text_path": str(raw_file),
        "status": "official_site_found_no_contact",
        "confidence": "low",
        "hunter_likelihood": "low",
        "hunter_likelihood_reason": "No explicit headhunter signal found.",
        "notes": "official site checked",
    }
