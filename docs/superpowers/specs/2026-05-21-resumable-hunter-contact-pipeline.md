# Spec: Resumable Japan Hunter Contact Pipeline

Generated: 2026-05-21
Status: Active
Owner: Codex
Operator entrypoint: Claude Code `/hunter-contact-backfill`

## Goal

Run one resumable 100-row batch at a time from the official MHLW `有料職業紹介事業` listing, enrich contact evidence, classify hunter likelihood, and safely accumulate results across long multi-day runs.

## Source Of Truth

- `README.md`: operator-facing command and output contract.
- `.claude/commands/hunter-contact-backfill.md`: Claude Code orchestration prompt.
- `data/processed/master.csv`: canonical machine-readable cumulative master.

The older `japan_headhunters_*` prototype outputs are legacy compatibility artifacts, not the current production output contract.

## Outputs

- `hunter_contacts.csv`: Chinese-header accepted hunter/contact CSV containing only `hunter_likelihood` values `high` and `medium`.
- `mhlw_placement_contacts_all.csv`: Chinese-header all-processed MHLW placement contact CSV, including `low` and `exclude`.
- `data/processed/master.csv`: canonical all-row machine CSV.
- `data/processed/master_zh.csv`: Chinese-header all-row master CSV.
- `data/processed/master_qa_report.md`: cumulative validation and duplicate-key report.
- `data/runs/<run_id>/`: per-run batch, static enrichment, agent queue/results, raw evidence, and batch QA.
- `data/manifest/mhlw_manifest.csv`: incremental MHLW row cache.
- `data/manifest/checkpoint.json`: MHLW crawl cursor.

## Batch Behavior

1. Ensure the local MHLW manifest cache has at least 100 rows not already present in `data/processed/master.csv`.
2. Prepare the next 100 unprocessed `record_id` rows.
3. Run deterministic static enrichment for each row.
4. Immediately queue unresolved rows for native Claude Code `hunter-contact-enricher` agents.
5. Keep no more than 5 active subagents.
6. Free a subagent slot only after the result JSONL, raw Dokobot evidence, and `.meta.json` pass the validator.
7. If validation fails, archive the bad result and either retry the same batch or quarantine it after the attempt limit.
8. Merge batch results only after all expected agent batches validate or are quarantined.
9. Upsert the batch into `master.csv` by `record_id`, then regenerate all human-facing CSVs and the master QA report.

## Field Semantics

- `source_url`: base source for the row, usually the MHLW detail page when the row enters the pipeline.
- `mhlw_source_url`: official MHLW verification page.
- `email_source_url`: public page where the email, contact form, or no-contact official-site evidence was confirmed.
- `email_source_text_path`: raw local evidence file for `email_source_url`.
- `hunter_likelihood`: one of `high`, `medium`, `low`, or `exclude`.
- `hunter_likelihood_reason`: short evidence-based explanation for the likelihood value.

## Quality Gates

- Every row in `master.csv` must pass schema validation.
- Potential duplicate contact keys are reported in `data/processed/master_qa_report.md`.
- `hunter_contacts.csv` excludes `low` and `exclude` rows.
- Agent results must validate before they count as complete:
  - known `record_id`
  - valid status/confidence/hunter likelihood
  - valid source URL fields
  - local Dokobot raw evidence path
  - sibling `.meta.json` proving `dokobot read --local --device ... --reuse-tab` succeeded
  - same-company evidence via company name token, phone, license number, or known official company domain
- `error` results do not count as complete. They are retried or quarantined.
- Quarantined batches preserve the static/MHLW row and do not merge bad agent evidence.

## Compliance Boundaries

Do not collect private social profiles, login-only data, paid database data, inferred email patterns, or personal non-business contact details. Prefer company-level channels and public company contact forms.
