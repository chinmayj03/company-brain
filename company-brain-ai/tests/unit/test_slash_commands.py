"""Unit tests for the slash command parser + registry (ADR-0052 P5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from companybrain.harness.commands import (
    SlashCommand,
    SlashCommandError,
    SlashCommandRegistry,
    load_default_commands,
    parse_and_render,
    parse_command_file,
)


def test_default_registry_loads_all_ten_commands():
    """All ten /<name> commands ship out of the box."""
    reg = load_default_commands()
    expected = {"extract", "query", "verify", "diff", "cost",
                "explain", "wipe", "stats", "init", "skills"}
    assert expected.issubset(set(reg.names))


def test_parse_command_file_extracts_metadata(tmp_path: Path):
    md = tmp_path / "demo.md"
    md.write_text(
        "---\n"
        "name: demo\n"
        "description: A demo command.\n"
        "args:\n"
        "  - name: target\n"
        "    type: string\n"
        "  - name: extras\n"
        "    type: string\n"
        "    required: false\n"
        "---\n"
        "Hello {target} and {extras}\n"
    )
    cmd = parse_command_file(md)
    assert cmd.name == "demo"
    assert cmd.description == "A demo command."
    assert [a.name for a in cmd.args] == ["target", "extras"]
    assert cmd.args[1].required is False


def test_render_substitutes_arguments():
    cmd = SlashCommand(
        name="demo", description="", body="extract {endpoint} {method}",
        args=[
            __import_arg("endpoint", required=True),
            __import_arg("method", required=False, default="GET"),
        ],
    )
    assert cmd.render("/v1/foo POST") == "extract /v1/foo POST"


def test_render_uses_default_for_optional_arg():
    cmd = SlashCommand(
        name="demo", description="", body="extract {endpoint} {method}",
        args=[
            __import_arg("endpoint", required=True),
            __import_arg("method", required=False, default="GET"),
        ],
    )
    assert cmd.render("/v1/foo") == "extract /v1/foo GET"


def test_render_raises_on_missing_required_arg():
    cmd = SlashCommand(
        name="demo", description="", body="extract {endpoint}",
        args=[__import_arg("endpoint", required=True)],
    )
    with pytest.raises(SlashCommandError):
        cmd.render("")


def test_parse_and_render_routes_to_command():
    rendered, name = parse_and_render("/extract /v1/foo POST")
    assert name == "extract"
    assert "/v1/foo" in rendered
    assert "POST" in rendered


def test_parse_and_render_passes_through_non_slash_message():
    rendered, name = parse_and_render("hello world")
    assert name is None
    assert rendered == "hello world"


def test_parse_and_render_unknown_command_raises():
    with pytest.raises(SlashCommandError):
        parse_and_render("/nope something")


def test_last_arg_eats_remainder():
    """The final declared arg captures all remaining tokens (free-form)."""
    cmd = SlashCommand(
        name="demo", description="", body="ask {question}",
        args=[__import_arg("question")],
    )
    assert cmd.render("what does Foo.bar do?") == "ask what does Foo.bar do?"


def test_registry_from_directory_handles_missing_dir(tmp_path: Path):
    reg = SlashCommandRegistry.from_directory(tmp_path / "doesntexist")
    assert reg.names == []


def __import_arg(name, *, type="string", required=True, default=None):
    from companybrain.harness.commands import SlashArg
    return SlashArg(name=name, type=type, required=required, default=default)
