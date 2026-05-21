---
description: Refresh the full MHLW Japan recruitment business manifest only.
argument-hint: "[--limit N for smoke]"
---

You are the main Claude Code operator for the `hunterdata` repository. Run from the repository root.

Goal:
- Refresh the official MHLW `有料職業紹介事業` manifest.
- Do not run email enrichment.
- Do not dispatch Claude subagents.
- Do not use Dokobot.

Run:

```bash
uv sync --python 3.12
uv run python -m scripts.mhlw_manifest --sleep-seconds 0.1
```

For a quick smoke test, run a limited refresh instead:

```bash
uv run python -m scripts.mhlw_manifest --limit 5 --sleep-seconds 0
```

Outputs:
- `data/manifest/mhlw_manifest.csv`
- `data/manifest/mhlw_manifest.jsonl`
- `data/manifest/checkpoint.json`
- raw official MHLW HTML under `data/raw/mhlw/`

After the full manifest exists, use `/hunter-contact-backfill` to process the next unprocessed batch of 100 rows.
