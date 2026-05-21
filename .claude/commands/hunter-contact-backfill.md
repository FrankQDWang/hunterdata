---
description: Stream Japan hunter contact backfill with native Claude Code agents and local Dokobot evidence.
argument-hint: "[target-count=100 agents=5]"
---

You are the main Claude Code orchestrator for the `hunterdata` repository. Run from the repository root. Do not use Codex and do not use `claude -p`.

Goal:
- Build the MHLW-verified company/phone/source dataset.
- Run deterministic conservative email/contact-form enrichment.
- As soon as deterministic enrichment cannot find an email, dispatch native Claude Code `hunter-contact-enricher` agents for those unresolved companies.
- Keep at most 5 active agents at once. This is an active-agent limit, not a wave size.
- Only dispatch a new prompt after one active agent has finished, written its JSONL result, and has local Dokobot raw evidence.
- Merge only after every expected result is complete and has raw Dokobot evidence.

Division of labor:
- Python owns deterministic work only: MHLW collection, conservative static enrichment, queue/prompt generation, status files, merge validation.
- Claude main agent owns orchestration: watch the queue, dispatch agents, track active slots, retry killed/incomplete agents.
- `hunter-contact-enricher` owns judgment work: given one company, find the best official site/contact evidence using its own reasoning and local Dokobot reads.
- Do not make the main agent solve contact enrichment itself. Do not replace the subagent with Bash `curl`, Python scraping, or hard-coded per-site logic.

Important operational rules:
- `TaskCreate` / `TaskUpdate` are todo tools, not subagent dispatch. Use the Claude Code `Agent` tool with `subagent_type: hunter-contact-enricher`. If the `Agent` tool is unavailable, stop with `BLOCKED: Agent tool unavailable`.
- Do not end the turn immediately after launching background agents. Keep monitoring until active agents complete or are killed. Background agents have been observed to be killed before writing results when the parent turn ends.
- Treat a task notification with `status: killed` as failed, even if it found useful evidence in its summary. Retry that exact prompt; do not merge summaries.
- The local Dokobot wrapper now lets Dokobot manage/reuse tabs by default. Do not add manual `open -a "Google Chrome"` calls.

1. Prepare MHLW rows:

```bash
uv sync --python 3.12
uv run python -m scripts.claude_agent_workflow \
  --target-count 100 \
  --mhlw-only \
  --mhlw-sleep-seconds 0.1
```

This writes the base MHLW-verified CSV files, including:
- `data/processed/japan_headhunters_contacts.csv`
- `data/processed/japan_headhunters_sources.csv`

2. Start streaming deterministic enrichment in the background:

```bash
mkdir -p data/interim/claude_agents
uv run python -m scripts.claude_agent_workflow \
  --target-count 100 \
  --stream-static-queue \
  --agents 5 \
  --candidate-limit 0 \
  --one-job-per-prompt \
  --static-sleep-seconds 0.1 \
  > data/interim/claude_agents/static-stream.log 2>&1 &
echo $! > data/interim/claude_agents/static-stream.pid
```

This process updates these files as it runs:
- `data/processed/japan_headhunters_contacts_static_enriched.csv`
- `data/processed/japan_headhunters_contacts_static_enriched_zh.csv`
- `data/interim/claude_agents/agent_queue.jsonl`
- `data/interim/claude_agents/stream_state.json`
- one prompt per unresolved company under `data/interim/claude_agents/prompts/`

3. Orchestrate native agents while the streaming process is still running:

- Poll `data/interim/claude_agents/agent_queue.jsonl`, `data/interim/claude_agents/prompts/`, and `data/interim/claude_agents/results/`.
- Maintain an in-memory set of active batch IDs.
- Dispatch only prompt files that are ready, not already active, and not already complete.
- Active count must never exceed 5.
- When an active agent writes its expected JSONL result and its referenced `source_text_path` plus `.meta.json` exist under `data/raw/claude_agents/`, remove it from active and dispatch the next ready prompt.
- If an active agent is killed, retries, or returns no result, keep the slot occupied until you retry or mark it failed explicitly. Do not silently advance.

When dispatching an agent, keep the prompt minimal. Give it the prompt file and the goal; let it decide how to search:

```
Process exactly this hunter-contact-enricher prompt:
data/interim/claude_agents/prompts/agent-NNN.md

Read the prompt and its batch JSONL. Find the exact company's public business email, or if no email is confidently available, its public inquiry/contact form. Use your own judgment to find the best official evidence page. You must use local Dokobot through scripts.dokobot_local_read for final evidence and write exactly one JSONL result to the required result path.
```

4. Keep the parent turn alive and monitor slots.

After each dispatch or notification, run short filesystem checks instead of ending the turn. Example checks:

```bash
find data/interim/claude_agents/results -type f -size +0 -maxdepth 1 | sort | wc -l
find data/raw/claude_agents -type f -name '*.meta.json' | sort | wc -l
cat data/interim/claude_agents/stream_state.json
```

Continue until:
- `stream_state.json` says `"done": true`
- every prompt in `data/interim/claude_agents/prompts/` has a matching non-empty result JSONL
- every non-error result has local Dokobot raw evidence and metadata

5. Merge and validate:

```bash
uv run python -m scripts.claude_agent_workflow --merge-only --target-count 100
uv run python -m pytest -q
```

Final outputs:
- `data/processed/japan_headhunters_contacts_agent_enriched.csv`
- `data/processed/japan_headhunters_contacts_agent_enriched_zh.csv`
- `data/processed/qa_report_agent_enriched.md`
