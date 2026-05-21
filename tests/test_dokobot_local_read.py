import subprocess

from scripts.dokobot_local_read import build_read_command, parse_local_device_id, read_with_local_dokobot


def test_parse_local_device_id_from_doko_list():
    output = """
Local:
  62882f0c-f9f1-49fc-83b4-c0f98b4d9ece  pid 23912, Chrome, ext 0.3.0
"""

    assert parse_local_device_id(output) == "62882f0c-f9f1-49fc-83b4-c0f98b4d9ece"


def test_build_read_command_uses_local_device_and_output_path(tmp_path):
    command = build_read_command(
        url="https://example.com/",
        output=tmp_path / "example.txt",
        device_id="62882f0c-f9f1-49fc-83b4-c0f98b4d9ece",
        timeout=120,
    )

    assert command[:6] == [
        "dokobot",
        "read",
        "--local",
        "--device",
        "62882f0c-f9f1-49fc-83b4-c0f98b4d9ece",
        "--timeout",
    ]
    assert "-o" in command
    assert command[-1] == "https://example.com/"


def test_build_read_command_can_reuse_visible_tab(tmp_path):
    command = build_read_command(
        url="https://example.com/",
        output=tmp_path / "example.txt",
        device_id="62882f0c-f9f1-49fc-83b4-c0f98b4d9ece",
        timeout=120,
        reuse_tab=True,
    )

    assert "--reuse-tab" in command


def test_read_with_local_dokobot_writes_meta(tmp_path):
    calls = []

    def runner(command, *, capture_output, text, check):
        calls.append(command)
        if command == ["dokobot", "doko", "list"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="Local:\n  62882f0c-f9f1-49fc-83b4-c0f98b4d9ece  pid 23912, Chrome, ext 0.3.0\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="Written to output\n", stderr="")

    output = tmp_path / "raw" / "example.txt"

    assert read_with_local_dokobot(url="https://example.com/", output=output, visible_tab=False, runner=runner) == 0

    meta = output.with_name("example.txt.meta.json").read_text(encoding="utf-8")
    assert '"mode": "local"' in meta
    assert '"returncode": 0' in meta
    assert calls[1][:5] == ["dokobot", "read", "--local", "--device", "62882f0c-f9f1-49fc-83b4-c0f98b4d9ece"]
