---
description: Orchestrate the full Japan hunter contact backfill with native Claude Code subagents and local Dokobot reads.
argument-hint: "[target-count=100 agents=5]"
---

You are the main Claude Code orchestrator for the `hunterdata` repository. Run from the repository root. Do not use Codex and do not use `claude -p`.

Goal:
- Build the MHLW-verified company/phone/source dataset.
- Run deterministic conservative email/contact-form enrichment.
- For rows still missing email, dispatch native Claude Code `hunter-contact-enricher` subagents.
- Keep at most 5 subagents running at once.
- Monitor that every subagent used local Dokobot through `scripts.dokobot_local_read`.
- Merge only after every expected result is complete and has raw Dokobot evidence.

Orchestration steps:

1. Prepare the deterministic stages:

```bash
uv sync --python 3.12
uv run python -m scripts.claude_agent_workflow \
  --target-count 100 \
  --agents 5 \
  --refresh-mhlw \
  --refresh-static \
  --claude-mode prompt-files \
  --candidate-limit 0 \
  --one-job-per-prompt
```

This writes:
- `data/processed/japan_headhunters_contacts.csv`
- `data/processed/japan_headhunters_contacts_static_enriched.csv`
- `data/processed/japan_headhunters_contacts_static_enriched_zh.csv`
- `data/interim/claude_agents/agent_queue.jsonl`
- one prompt per unresolved company under `data/interim/claude_agents/prompts/`

2. Read `data/interim/claude_agents/agent_queue.jsonl` and list prompt files under `data/interim/claude_agents/prompts/`.

3. Dispatch native Claude Code subagents, not shell-launched Claude sessions:
- Use the `hunter-contact-enricher` agent.
- Give each subagent exactly one prompt file.
- Keep at most 5 active subagents at a time.
- When one finishes, dispatch the next pending prompt.
- Each subagent must use:

```bash
uv run python -m scripts.dokobot_local_read "<url>" -o "data/raw/claude_agents/<batch>/<record>.txt" --timeout 120
```

The wrapper calls `dokobot doko list`, selects the local Chrome device, opens a visible Chrome tab, runs `dokobot read --local --device ... --reuse-tab`, and writes a sibling `.meta.json` file. Do not accept raw evidence from curl, requests, remote Dokobot, or a headless browser.

4. Monitor progress:
- Result files must appear under `data/interim/claude_agents/results/`.
- Raw Dokobot text files and `.meta.json` files must appear under `data/raw/claude_agents/`.
- If a subagent finishes without a result, without `source_text_path`, without raw text, or without `.meta.json`, retry only that prompt with `hunter-contact-enricher`.
- Do not edit final CSV files manually.

5. Merge and validate:

```bash
uv run python -m scripts.claude_agent_workflow --merge-only --target-count 100
uv run python -m pytest -q
```

The merge command is intentionally strict. It fails if any expected agent result is incomplete, if a `record_id` is unknown, or if a result lacks local Dokobot raw evidence.

Final outputs:
- `data/processed/japan_headhunters_contacts_agent_enriched.csv`
- `data/processed/japan_headhunters_contacts_agent_enriched_zh.csv`
- `data/processed/qa_report_agent_enriched.md`
