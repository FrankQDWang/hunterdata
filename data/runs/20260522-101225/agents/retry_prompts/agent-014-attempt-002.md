Retry this hunter-contact-enricher job.

Previous validation failure: agent result for company-a77dfeed does not prove same company in raw evidence: expected company name, phone, license number, or known company domain

Do not repeat the failed evidence path unless it clearly proves the exact company.
Write the corrected JSONL to the same output path required by the original prompt.

Process this hunter-contact-enricher job.

The agent definition contains the task rules, acceptance criteria, Dokobot requirement, and output schema. This file is only the job envelope.

Input JSONL:
`data/runs/20260522-101225/agents/batches/agent-014.jsonl`

Output JSONL:
`data/runs/20260522-101225/agents/results/agent-014-results.jsonl`

Raw Dokobot evidence directory:
`data/runs/20260522-101225/raw/agents/agent-014/`

Read the input JSONL, use its deterministic context as starting evidence, find the exact company's public business email or inquiry/contact form, judge `hunter_likelihood`, and write exactly one JSON object per input job to the output JSONL.
