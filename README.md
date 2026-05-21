# Japan Headhunter Contact Research

This project builds a local, source-traceable dataset of 100 Japan recruitment/headhunter contact records.

## Outputs

- `data/processed/japan_headhunters_contacts.csv`
- `data/processed/japan_headhunters_contacts.jsonl`
- `data/processed/japan_headhunters_sources.csv`
- `data/processed/qa_report.md`
- `data/processed/japan_headhunters_contacts_static_enriched.csv`
- `data/processed/japan_headhunters_contacts_static_enriched_zh.csv`
- `data/processed/japan_headhunters_contacts_agent_enriched.csv`
- `data/processed/japan_headhunters_contacts_agent_enriched_zh.csv`

## Requirements

- `uv`
- Python 3.12 managed by `uv`
- Dokobot CLI (`dokobot --help` should work)
- Chrome + Dokobot local bridge if using local Dokobot reads
- Claude Code CLI for the optional agent backfill (`claude auth status` should succeed)
- Claude Code may require a one-time workspace trust confirmation; run `claude` once from the repository root and trust the folder before using `/hunter-contact-backfill`.

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
uv run python -m scripts.mhlw_collect --target-count 100
uv run python -m scripts.qa_report data/processed/japan_headhunters_contacts.csv --expected-count 100
```

The MHLW collector uses the public `人材サービス総合サイト` search and detail pages for `有料職業紹介事業`, saves raw official HTML under `data/raw/mhlw/`, writes `data/interim/candidates.jsonl`, and then emits the processed contacts, source audit CSV, JSONL, and QA report.

## Contact Enrichment Pipeline

The recommended workflow is:

1. collect MHLW verified companies and phones;
2. run deterministic static enrichment for public emails and `お問い合わせ` forms;
3. send unresolved rows to native Claude Code subagents, one unresolved company per prompt, with at most 5 running at once;
4. require every subagent result to include raw local Dokobot evidence created by `scripts.dokobot_local_read`;
5. merge agent JSONL results by `record_id`.

Recommended Claude Code flow for a fresh clone:

```bash
claude
```

Then run the project slash command:

```text
/hunter-contact-backfill
```

The slash command is the main orchestrator prompt. It runs the deterministic Python stages, dispatches native `hunter-contact-enricher` subagents, monitors raw Dokobot evidence, and runs the strict merge. It does not use `claude -p`, and Python is not responsible for managing Claude subagents.
All default paths are relative to the repository directory where you start Claude Code, so outputs land under this project's `data/` directory.

For a real one-row smoke test before running the full queue:

```bash
uv run python -m scripts.claude_agent_workflow \
  --target-count 100 \
  --agents 1 \
  --claude-mode prompt-files \
  --candidate-limit 0 \
  --one-job-per-prompt \
  --max-agent-jobs 1
```

Then open the single generated prompt under `data/interim/claude_agents/prompts/` with the `hunter-contact-enricher` agent. The subagent should call:

```bash
uv run python -m scripts.dokobot_local_read "<url>" -o "data/raw/claude_agents/<batch>/<record>.txt" --timeout 120
```

This wrapper opens a visible Chrome tab, calls the local Chrome bridge with `dokobot read --local --device ... --reuse-tab`, and writes a sibling `.meta.json` audit file.

If you want to prepare prompt files manually:

```bash
uv run python -m scripts.claude_agent_workflow --target-count 100 --agents 5 --refresh-static --candidate-limit 0 --one-job-per-prompt
```

Then run the generated prompt files under `data/interim/claude_agents/prompts/` with the `hunter-contact-enricher` agent. After all result files are written under `data/interim/claude_agents/results/`, merge:

```bash
uv run python -m scripts.claude_agent_workflow --merge-only
```

Final files:

- `data/processed/japan_headhunters_contacts_agent_enriched.csv`
- `data/processed/japan_headhunters_contacts_agent_enriched_zh.csv`

If you already have a manually curated candidate manifest, run:

```bash
uv run python -m scripts.collect_contacts --target-count 100 --use-dokobot-local
uv run python -m scripts.qa_report data/processed/japan_headhunters_contacts.csv --expected-count 100
```
