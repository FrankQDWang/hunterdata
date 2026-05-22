from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backfill_command_declares_strong_process_gates():
    prompt = (ROOT / ".claude" / "commands" / "hunter-contact-backfill.md").read_text(encoding="utf-8")

    assert "BATCH_SIZE" in prompt
    assert "AGENT_LIMIT" in prompt
    assert "If the static stream exits non-zero" in prompt
    assert "Acceptance checklist before merge" in prompt
    assert "--agent-status" in prompt
    assert "valid` or `quarantined" in prompt
    assert "python3 scripts/hunter_preflight.py" in prompt
    assert "Do not create run directories" in prompt
    assert "Do not override the main agent model" in prompt
    assert "model: haiku" in prompt


def test_hunter_contact_enricher_prompt_declares_exploration_boundaries():
    prompt = (ROOT / ".claude" / "agents" / "hunter-contact-enricher.md").read_text(encoding="utf-8")

    assert "Goal" in prompt
    assert "Boundaries" in prompt
    assert "Non-goals" in prompt
    assert "Acceptance criteria" in prompt
    assert "Do not infer email patterns" in prompt
    assert "Do not use search result snippets" in prompt
    assert "Do not treat the MHLW license alone as headhunter evidence" in prompt
    assert "source_url must match the Dokobot metadata URL" in prompt
    assert "model: haiku" in prompt


def test_readme_documents_clean_baseline_and_desktop_limits():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Data Handoff And Reset" in readme
    assert "Generated business data is intentionally tracked by git" in readme
    assert "git add data hunter_contacts.csv mhlw_placement_contacts_all.csv" in readme
    assert "Claude Desktop" in readme
    assert "Claude Code CLI" in readme
    assert "Claude Code Desktop" in readme
    assert "same underlying Claude Code engine" in readme
    assert "Claude Desktop Chat and Claude Cowork are not the same" in readme
    assert "python3 scripts/hunter_preflight.py" in readme
    assert "Dokobot Chrome plugin/device connection" in readme
    assert "model: haiku" in readme


def test_gitignore_tracks_resumable_business_data():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".venv/" in gitignore
    assert ".pytest_cache/" in gitignore
    assert "__pycache__/" in gitignore
    assert "data/manifest/*" not in gitignore
    assert "data/runs/*" not in gitignore
    assert "data/raw/mhlw/*" not in gitignore
    assert "data/processed/*.csv" not in gitignore
    assert "hunter_contacts.csv" not in gitignore
    assert "mhlw_placement_contacts_all.csv" not in gitignore
