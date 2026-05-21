import subprocess

import pytest

from scripts.dokobot_client import DokobotClient, DokobotError


def fixed_timestamp():
    return "20260521T030000Z"


def test_read_uses_local_flag_and_output_file(tmp_path):
    commands = []

    def runner(command, *, timeout, capture_output, text, check):
        commands.append(command)
        output_path = command[command.index("-o") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("rendered text")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    client = DokobotClient(raw_dir=tmp_path, local=True, runner=runner, timestamp=fixed_timestamp)
    result = client.read("https://example.com/contact")

    assert commands[0][:3] == ["dokobot", "read", "--local"]
    assert result.path.read_text(encoding="utf-8") == "rendered text"
    assert result.url == "https://example.com/contact"


def test_search_captures_stdout_to_raw_file(tmp_path):
    def runner(command, *, timeout, capture_output, text, check):
        assert command == ["dokobot", "search", "--num", "3", "recruitment japan"]
        return subprocess.CompletedProcess(command, 0, stdout="result text", stderr="")

    client = DokobotClient(raw_dir=tmp_path, runner=runner, timestamp=fixed_timestamp)
    result = client.search("recruitment japan", num=3)

    assert result.path.read_text(encoding="utf-8") == "result text"


def test_dokobot_error_persists_failure_context(tmp_path):
    def runner(command, *, timeout, capture_output, text, check):
        raise subprocess.CalledProcessError(503, command, output="out", stderr="no extension")

    client = DokobotClient(raw_dir=tmp_path, runner=runner, timestamp=fixed_timestamp)

    with pytest.raises(DokobotError) as excinfo:
        client.search("recruitment japan")

    assert "no extension" in str(excinfo.value)
    assert excinfo.value.error_path is not None
    assert "no extension" in excinfo.value.error_path.read_text(encoding="utf-8")
