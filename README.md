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
uv run python -m scripts.mhlw_collect --target-count 100
uv run python -m scripts.qa_report data/processed/japan_headhunters_contacts.csv --expected-count 100
```

The MHLW collector uses the public `人材サービス総合サイト` search and detail pages for `有料職業紹介事業`, saves raw official HTML under `data/raw/mhlw/`, writes `data/interim/candidates.jsonl`, and then emits the processed contacts, source audit CSV, JSONL, and QA report.

If you already have a manually curated candidate manifest, run:

```bash
uv run python -m scripts.collect_contacts --target-count 100 --use-dokobot-local
uv run python -m scripts.qa_report data/processed/japan_headhunters_contacts.csv --expected-count 100
```
