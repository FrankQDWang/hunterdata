---
description: Run one resumable 100-row Japan hunter contact batch: incrementally fetch MHLW rows, enrich deterministically, then use native Claude Code agents only for unresolved contacts.
argument-hint: "[batch-size=100 agents=5]"
---

You are the main Claude Code orchestrator for the `hunterdata` repository. Run from the repository root. Do not use Codex and do not use `claude -p`.

Goal:
- Process one batch of the next 100 unprocessed MHLW rows.
- If the local manifest cache does not already contain 100 unprocessed rows, incrementally fetch more official MHLW search/detail rows first.
- Default batch size is 100. Default active agent limit is 5.
- First run deterministic conservative email/contact-form enrichment.
- Keep and update `hunter_likelihood` plus `hunter_likelihood_reason` for every row.
- As soon as deterministic enrichment finds neither a public email nor a contact form, dispatch one native Claude Code `hunter-contact-enricher` subagent for that unresolved company.
- Keep at most 5 active agents at once. This is an active-agent slot limit, not a dispatch wave size.
- Free an agent slot only after that subagent is done/closed and its expected JSONL result plus local Dokobot raw evidence have been validated.
- Merge only after every expected agent result is complete and has raw Dokobot evidence.

Division of labor:
- Python owns deterministic work only: incremental MHLW official crawl, batch selection, static enrichment, queue/prompt generation, merge validation, master upsert.
- Claude main agent owns orchestration: start the background static queue, watch prompt/result/raw files, dispatch subagents, track active slots, retry killed/incomplete agents, and close finished agents.
- `hunter-contact-enricher` owns judgment work: given one company, find the best official site/contact evidence and judge hunter likelihood using its own reasoning and local Dokobot reads.
- Do not make the main agent solve contact enrichment itself. Do not replace the subagent with Bash `curl`, Python scraping, or hard-coded per-site logic.

Important operational rules:
- Do not require a full MHLW manifest refresh before this command. The command owns incremental MHLW crawling for this batch.
- `data/manifest/mhlw_manifest.csv` is a local cache of official rows discovered so far, not necessarily the full 34k+ MHLW list.
- `TaskCreate` / `TaskUpdate` are todo tools, not subagent dispatch. Use the Claude Code `Agent` tool with `subagent_type: hunter-contact-enricher`. If the `Agent` tool is unavailable, stop with `BLOCKED: Agent tool unavailable`.
- Do not end the turn immediately after launching background agents. Keep monitoring until active agents complete or are killed. Background agents can be killed before writing results when the parent turn ends.
- Treat a task notification with `status: killed` as failed, even if it found useful evidence in its summary. Retry that exact prompt; do not merge summaries.
- The local Dokobot wrapper lets Dokobot manage/reuse tabs by default. Do not add manual `open -a "Google Chrome"` calls.
- Static enrichment that finds either an email or a contact form is considered complete for this batch and must not be sent to an agent.

1. Incrementally fetch official MHLW rows as needed, then prepare the next resumable batch:

```bash
uv sync --python 3.12
RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="data/runs/${RUN_ID}"
mkdir -p "${RUN_DIR}"
uv run python -m scripts.hunter_resume prepare-next-batch \
  --ensure-mhlw \
  --manifest-csv data/manifest/mhlw_manifest.csv \
  --manifest-jsonl data/manifest/mhlw_manifest.jsonl \
  --manifest-checkpoint data/manifest/checkpoint.json \
  --mhlw-raw-dir data/raw/mhlw \
  --master-csv data/processed/master.csv \
  --batch-csv "${RUN_DIR}/batch.csv" \
  --limit 100 \
  --mhlw-sleep-seconds 0.1
BATCH_COUNT="$(( $(wc -l < "${RUN_DIR}/batch.csv") - 1 ))"
```

If this prints `rows=0`, there is no remaining official row to process from the current MHLW listing. Stop and report that the master output is complete for the official listing reached by the checkpoint.

2. Start streaming deterministic enrichment in the background:

```bash
mkdir -p "${RUN_DIR}/agents" "${RUN_DIR}/raw/static" "${RUN_DIR}/raw/agents"
uv run python -m scripts.claude_agent_workflow \
  --stream-static-queue \
  --base-csv "${RUN_DIR}/batch.csv" \
  --static-csv "${RUN_DIR}/static.csv" \
  --static-zh-csv "${RUN_DIR}/static_zh.csv" \
  --static-raw-dir "${RUN_DIR}/raw/static" \
  --agent-dir "${RUN_DIR}/agents" \
  --agent-raw-dir "${RUN_DIR}/raw/agents" \
  --agent-when no_email_or_form \
  --candidate-limit 0 \
  --one-job-per-prompt \
  --static-sleep-seconds 0.1 \
  > "${RUN_DIR}/static-stream.log" 2>&1 &
echo $! > "${RUN_DIR}/static-stream.pid"
```

This process updates:
- `${RUN_DIR}/static.csv`
- `${RUN_DIR}/static_zh.csv`
- `${RUN_DIR}/agents/agent_queue.jsonl`
- `${RUN_DIR}/agents/stream_state.json`
- one prompt per unresolved company under `${RUN_DIR}/agents/prompts/`

3. Orchestrate native agents while the streaming process is still running:

- Poll `${RUN_DIR}/agents/prompts/`, `${RUN_DIR}/agents/results/`, `${RUN_DIR}/agents/stream_state.json`, and `${RUN_DIR}/raw/agents/`.
- Maintain an in-memory set of active batch IDs.
- Dispatch only prompt files that are ready, not already active, and not already complete.
- Active count must never exceed 5.
- When an active agent writes its expected JSONL result and its referenced `source_text_path` plus `.meta.json` exist under `${RUN_DIR}/raw/agents/`, remove it from active and dispatch the next ready prompt.
- If an active agent is killed, retries, or returns no result, keep the slot occupied until you retry or mark it failed explicitly. Do not silently advance.
- Close/recycle each finished subagent after validating its result and raw evidence, then dispatch the next queued prompt if available.

When dispatching an agent, keep the prompt minimal. Give it the prompt file and the goal; let the subagent decide how to search:

```text
Process exactly this hunter-contact-enricher prompt:
${RUN_DIR}/agents/prompts/agent-NNN.md

Read the prompt and its batch JSONL. Find the exact company's public business email, or if no email is confidently available, its public inquiry/contact form. Also set hunter_likelihood to high, medium, low, or exclude with a short reason. Use your own judgment to find the best official evidence page. You must use local Dokobot through scripts.dokobot_local_read for final evidence and write exactly one JSONL result to the required result path.
```

4. Keep the parent turn alive and monitor slots.

After each dispatch or notification, run short filesystem checks instead of ending the turn. Example checks:

```bash
find "${RUN_DIR}/agents/results" -maxdepth 1 -type f -size +0 | sort | wc -l
find "${RUN_DIR}/raw/agents" -type f -name '*.meta.json' | sort | wc -l
cat "${RUN_DIR}/agents/stream_state.json"
tail -n 20 "${RUN_DIR}/static-stream.log"
```

Continue until:
- `${RUN_DIR}/agents/stream_state.json` says `"done": true`
- every prompt in `${RUN_DIR}/agents/prompts/` has a matching non-empty result JSONL
- every non-error result has local Dokobot raw evidence and metadata

5. Merge the batch and update the master output:

```bash
uv run python -m scripts.claude_agent_workflow \
  --merge-only \
  --target-count "${BATCH_COUNT}" \
  --static-csv "${RUN_DIR}/static.csv" \
  --agent-dir "${RUN_DIR}/agents" \
  --agent-raw-dir "${RUN_DIR}/raw/agents" \
  --final-csv "${RUN_DIR}/final.csv" \
  --final-zh-csv "${RUN_DIR}/final_zh.csv" \
  --final-qa-path "${RUN_DIR}/qa_report.md"

uv run python -m scripts.hunter_resume upsert-master \
  --batch-csv "${RUN_DIR}/final.csv" \
  --master-csv data/processed/master.csv \
  --master-zh-csv data/processed/master_zh.csv \
  --root-csv hunter_contacts.csv

uv run python -m pytest -q
```

Final human-facing output:
- `hunter_contacts.csv`

Machine/debug outputs:
- `data/processed/master.csv`
- `data/processed/master_zh.csv`
- `data/manifest/mhlw_manifest.csv`
- `data/manifest/checkpoint.json`
- `${RUN_DIR}/batch.csv`
- `${RUN_DIR}/static.csv`
- `${RUN_DIR}/final.csv`
- `${RUN_DIR}/qa_report.md`
- `${RUN_DIR}/agents/`
- `${RUN_DIR}/raw/`
