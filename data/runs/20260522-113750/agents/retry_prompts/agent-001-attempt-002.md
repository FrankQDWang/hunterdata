Retry this hunter-contact-enricher job.

Previous validation failure: status official_site_found_no_contact but includes email/contact_form_url

Do not repeat the failed evidence path unless it clearly proves the exact company.
Write the corrected JSONL to the same output path required by the original prompt.

Process this hunter-contact-enricher job.

The agent definition contains the task rules, acceptance criteria, Dokobot requirement, and output schema. This file is only the job envelope.

Input JSONL:
`data/runs/20260522-113750/agents/batches/agent-001.jsonl`

Output JSONL:
`data/runs/20260522-113750/agents/results/agent-001-results.jsonl`

Raw Dokobot evidence directory:
`data/runs/20260522-113750/raw/agents/agent-001/`

Read the input JSONL, use its deterministic context as starting evidence, find the exact company's public business email or inquiry/contact form, judge `hunter_likelihood`, and write exactly one JSON object per input job to the output JSONL.
