"""Acceptance tests for ADR-0051 Phase 3 — skills + per-repo memory.

These tests assert the headline P3 properties end-to-end:

  1. Detection picks the right framework on Spring vs FastAPI repos
     (the prompt's "same extraction works on Spring AND FastAPI" goal).
  2. The harness telemetry surfaces `skill_loaded` so the orchestrator
     and downstream callers can tell which skill ran.
  3. The framework SKILL.md content is actually injected into the system
     prompt (smoke-test that the agent saw the framework guidance).
  4. BRAIN.md is auto-loaded into the prompt when present.
  5. `memory.auto_append` survives multiple identical calls — the
     "JsonKeyMapping always dropped" pattern from the implementation
     prompt's acceptance test.

LLM calls are scripted via a fake provider so the suite is deterministic
and runs without API credentials.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from companybrain.harness import memory
from companybrain.harness.loop import HarnessLoop
from companybrain.harness.skills import detect_framework
from companybrain.harness.system_prompt import build_system_prompt
from companybrain.llm.base import ChatResponse, TaskRole

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def spring_repo(tmp_path: Path) -> Path:
    """Tiny Spring Boot repo: pom + a controller class with @SpringBootApplication."""
    base = tmp_path / "src" / "main" / "java" / "com" / "ex"
    base.mkdir(parents=True)
    (tmp_path / "pom.xml").write_text(
        "<project><dependencies>"
        "<dependency><groupId>org.springframework.boot</groupId>"
        "<artifactId>spring-boot-starter-web</artifactId></dependency>"
        "</dependencies></project>"
    )
    (base / "Application.java").write_text(
        "package com.ex;\n"
        "import org.springframework.boot.autoconfigure.SpringBootApplication;\n"
        "@SpringBootApplication\n"
        "public class Application {}\n"
    )
    return tmp_path


@pytest.fixture
def fastapi_repo(tmp_path: Path) -> Path:
    """Tiny FastAPI repo: pyproject + a main.py with `from fastapi import FastAPI`."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["fastapi>=0.110", "uvicorn"]\n'
    )
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
        "@app.get('/health')\nasync def health():\n    return {'ok': True}\n"
    )
    return tmp_path


# ── 1. Framework detection on Spring vs FastAPI ─────────────────────────────

def test_spring_repo_detects_as_spring_boot(spring_repo: Path):
    assert detect_framework(spring_repo) == "spring-boot"


def test_fastapi_repo_detects_as_fastapi(fastapi_repo: Path):
    assert detect_framework(fastapi_repo) == "fastapi"


# ── 2. system_prompt injects the skill ───────────────────────────────────────

def test_system_prompt_includes_spring_skill_for_spring_repo(spring_repo: Path):
    """The Spring-specific guidance lands inside the system prompt verbatim."""
    context: dict = {
        "repo_path": str(spring_repo),
        "workspace_id": "ws", "endpoint_path": "/x", "http_method": "GET",
    }
    prompt = build_system_prompt(context)

    assert "# Framework Skill: spring-boot" in prompt
    assert "@RestController" in prompt          # spring-boot/SKILL.md content
    assert context["skill_loaded"] == "spring-boot"


def test_system_prompt_includes_fastapi_skill_for_fastapi_repo(fastapi_repo: Path):
    context: dict = {
        "repo_path": str(fastapi_repo),
        "workspace_id": "ws", "endpoint_path": "/health", "http_method": "GET",
    }
    prompt = build_system_prompt(context)

    assert "# Framework Skill: fastapi" in prompt
    assert "Pydantic" in prompt                 # fastapi/SKILL.md content
    assert context["skill_loaded"] == "fastapi"


def test_system_prompt_loads_no_skill_for_unrecognised_repo(tmp_path: Path):
    """A directory with no markers gets no framework skill section."""
    (tmp_path / "README.md").write_text("# nothing here\n")
    context: dict = {
        "repo_path": str(tmp_path),
        "workspace_id": "ws", "endpoint_path": "/x", "http_method": "GET",
    }
    prompt = build_system_prompt(context)

    assert "# Framework Skill:" not in prompt
    assert context["skill_loaded"] is None


# ── 3. BRAIN.md auto-loads into the prompt ──────────────────────────────────

def test_system_prompt_includes_brain_md_when_present(spring_repo: Path):
    """An existing .brain/BRAIN.md is appended to the prompt under its heading."""
    brain = spring_repo / ".brain"
    brain.mkdir()
    (brain / "BRAIN.md").write_text(
        "# BRAIN.md\n\n"
        "## Curated notes\n"
        "- The lob column was renamed in 2024-Q3.\n"
    )

    context: dict = {
        "repo_path": str(spring_repo),
        "workspace_id": "ws", "endpoint_path": "/x", "http_method": "GET",
    }
    prompt = build_system_prompt(context)

    assert "# Repo memory (BRAIN.md)" in prompt
    assert "lob column was renamed" in prompt
    assert context["brain_md_loaded"] is True


def test_system_prompt_brain_md_section_absent_when_no_file(spring_repo: Path):
    context: dict = {
        "repo_path": str(spring_repo),
        "workspace_id": "ws", "endpoint_path": "/x", "http_method": "GET",
    }
    prompt = build_system_prompt(context)

    assert "# Repo memory (BRAIN.md)" not in prompt
    assert context["brain_md_loaded"] is False


# ── 4. HarnessLoop telemetry surfaces skill_loaded + brain_md_loaded ────────

def _resp(content: str = "", *, tool_calls=None) -> ChatResponse:
    return ChatResponse(
        content=content, model="mock-model", provider="anthropic",
        input_tokens=80, output_tokens=20, tool_calls=list(tool_calls or []),
    )


class _FakeProvider:
    """Replays a single text-only response so HarnessLoop terminates immediately."""

    provider_name = "anthropic"

    def __init__(self, content: str = "done"):
        self._content = content

    async def chat_with_tools(self, **kwargs):
        return _resp(self._content)

    def model_for_role(self, role: TaskRole) -> str:
        return "claude-haiku-4-5-20251001"


async def test_harness_telemetry_reports_spring_skill(spring_repo: Path):
    """A run on the Spring fixture surfaces `skill_loaded == "spring-boot"`."""
    loop = HarnessLoop(provider=_FakeProvider("done"), max_iterations=2)
    result = await loop.run(
        "Extract the health endpoint.",
        context={
            "repo_path": str(spring_repo),
            "workspace_id": "ws", "endpoint_path": "/x", "http_method": "GET",
        },
    )

    assert result.telemetry["skill_loaded"] == "spring-boot"
    assert result.telemetry["brain_md_loaded"] is False


async def test_harness_telemetry_reports_fastapi_skill_and_brain_md(fastapi_repo: Path):
    """A run on the FastAPI fixture, with a BRAIN.md, surfaces both flags."""
    brain = fastapi_repo / ".brain"
    brain.mkdir()
    (brain / "BRAIN.md").write_text("# BRAIN.md\n## Curated\n- skip JsonKeyMapping\n")

    loop = HarnessLoop(provider=_FakeProvider("done"), max_iterations=2)
    result = await loop.run(
        "Extract /health.",
        context={
            "repo_path": str(fastapi_repo),
            "workspace_id": "ws", "endpoint_path": "/health", "http_method": "GET",
        },
    )

    assert result.telemetry["skill_loaded"] == "fastapi"
    assert result.telemetry["brain_md_loaded"] is True


# ── 5. BRAIN.md auto-append (the implementation-prompt acceptance) ──────────

def test_brain_memory_auto_appends_recurring_observations(tmp_path: Path):
    """Drop the same observation three runs in a row; BRAIN.md mentions it once.

    Mirrors the acceptance scenario described in the implementation prompt:
    the pipeline auto-appends "JsonKeyMapping always dropped" three runs in
    a row; dedupe collapses those into a single bullet.
    """
    for _ in range(3):
        memory.auto_append(tmp_path, "JsonKeyMapping always dropped")

    bm = (tmp_path / ".brain" / "BRAIN.md").read_text()
    # The template seeds with a JsonKeyMapping example in its commented-out
    # examples block, so we look for the full appended phrase rather than
    # the bare class name.
    assert "JsonKeyMapping always dropped" in bm
    assert bm.count("JsonKeyMapping always dropped") == 1   # dedupe within window


def test_brain_md_auto_section_round_trip_into_prompt(tmp_path: Path):
    """Auto-appended observations are visible to the agent on the next run.

    1. Pipeline appends an observation via memory.auto_append.
    2. build_system_prompt reads .brain/BRAIN.md and stitches it in.
    3. The agent's system prompt now contains the observation.
    """
    # Stand up a minimal FastAPI repo so a skill *also* loads — proves the
    # two attachments coexist rather than overwriting each other.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["fastapi"]\n'
    )

    memory.auto_append(tmp_path, "Skip JsonKeyMapping always — constants table")

    context: dict = {
        "repo_path": str(tmp_path),
        "workspace_id": "ws", "endpoint_path": "/x", "http_method": "GET",
    }
    prompt = build_system_prompt(context)

    assert "# Framework Skill: fastapi" in prompt
    assert "# Repo memory (BRAIN.md)" in prompt
    assert "Skip JsonKeyMapping" in prompt
