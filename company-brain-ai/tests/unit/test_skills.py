"""Unit tests for the framework skill detector and loader (ADR-0051 P3).

The detector is a cheap deterministic file-marker scan; these tests build
toy repos in tmp_path and assert that the right framework wins, that
empty / unknown repos return None, and that the skill loader reads the
shipped SKILL.md files for every supported framework.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from companybrain.harness import skills
from companybrain.harness.skills import (
    AVAILABLE_FRAMEWORKS,
    detect_framework,
    load_skill,
)

# ── detect_framework ────────────────────────────────────────────────────────

def test_detect_returns_none_for_empty_repo(tmp_path: Path):
    """A directory with no recognisable markers returns None — no skill loaded."""
    (tmp_path / "README.md").write_text("# nothing here\n")
    assert detect_framework(tmp_path) is None


def test_detect_returns_none_for_nonexistent_path(tmp_path: Path):
    """A path that does not exist returns None instead of raising."""
    assert detect_framework(tmp_path / "does-not-exist") is None


def test_detect_returns_none_for_file_path(tmp_path: Path):
    """If repo_path points at a file rather than a directory, return None."""
    f = tmp_path / "x.txt"
    f.write_text("hi")
    assert detect_framework(f) is None


def test_detect_spring_boot_via_annotation(tmp_path: Path):
    """A Java file containing @SpringBootApplication is enough to win."""
    pkg = tmp_path / "src" / "main" / "java" / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "Application.java").write_text(
        "package com.example;\n@SpringBootApplication public class Application {}\n"
    )
    assert detect_framework(tmp_path) == "spring-boot"


def test_detect_spring_boot_via_pom(tmp_path: Path):
    """pom.xml with spring-boot-starter alone is sufficient."""
    (tmp_path / "pom.xml").write_text(
        "<project><dependencies>"
        "<dependency><artifactId>spring-boot-starter-web</artifactId></dependency>"
        "</dependencies></project>"
    )
    assert detect_framework(tmp_path) == "spring-boot"


def test_detect_fastapi_via_import(tmp_path: Path):
    """Python file with `from fastapi import FastAPI` wins for FastAPI."""
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    assert detect_framework(tmp_path) == "fastapi"


def test_detect_fastapi_via_pyproject(tmp_path: Path):
    """pyproject.toml listing fastapi is enough."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=["fastapi>=0.110"]\n'
    )
    assert detect_framework(tmp_path) == "fastapi"


def test_detect_nestjs_via_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"@nestjs/core": "^10.0.0", "@nestjs/common": "^10.0.0"}}\n'
    )
    assert detect_framework(tmp_path) == "nestjs"


def test_detect_django_via_manage_py(tmp_path: Path):
    (tmp_path / "manage.py").write_text("# django manage.py\nimport django\n")
    (tmp_path / "settings.py").write_text("from django.conf import settings\n")
    assert detect_framework(tmp_path) == "django"


def test_detect_rails_via_gemfile(tmp_path: Path):
    (tmp_path / "Gemfile").write_text(
        "source 'https://rubygems.org'\ngem 'rails', '~> 7.0'\n"
    )
    assert detect_framework(tmp_path) == "rails"


def test_detect_nextjs_via_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"next": "14.0.0", "react": "18.0.0"}}\n'
    )
    assert detect_framework(tmp_path) == "nextjs"


def test_detect_picks_winner_by_majority(tmp_path: Path):
    """Two FastAPI markers + one Django marker → fastapi wins.

    The detector sums marker hits per framework; whichever has more wins.
    Mixed-language repos still need to pick one skill.
    """
    # FastAPI: pyproject + 1 .py file = 2 hits
    (tmp_path / "pyproject.toml").write_text("[project]\ndependencies=['fastapi']\n")
    (tmp_path / "api.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    # Django: 1 .py file = 1 hit (no manage.py)
    (tmp_path / "models.py").write_text("from django.db import models\n")
    assert detect_framework(tmp_path) == "fastapi"


def test_detect_skips_node_modules(tmp_path: Path):
    """A nested node_modules with FastAPI-shaped strings must NOT count.

    rglob doesn't honour .gitignore, so the scanner has its own skip-list.
    Without it, a fastembed dependency or similar could trip the detector
    on a Java repo.
    """
    nm = tmp_path / "node_modules" / "some-pkg"
    nm.mkdir(parents=True)
    (nm / "config.py").write_text("from fastapi import FastAPI\n")  # would match if scanned
    # Real signal: a Spring repo
    (tmp_path / "pom.xml").write_text(
        "<project><dependency><artifactId>spring-boot-starter</artifactId></dependency></project>"
    )
    assert detect_framework(tmp_path) == "spring-boot"


def test_detect_caps_per_pattern_scan(tmp_path: Path, monkeypatch):
    """The scan stops after the cap so detection stays cheap on big repos.

    We monkey-patch the cap to 3 and assert the scan reads no more than
    that many files even when 10 candidates exist.
    """
    monkeypatch.setattr(skills, "_MAX_FILES_PER_PATTERN", 3, raising=True)

    pkg = tmp_path / "src"
    pkg.mkdir()
    for i in range(10):
        (pkg / f"f{i}.py").write_text("from fastapi import FastAPI\n")

    reads: list[Path] = []
    real_read = Path.read_text

    def counting_read(self, *args, **kwargs):
        reads.append(self)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read)
    fw = detect_framework(tmp_path)
    assert fw == "fastapi"
    # Multiple frameworks scan **/*.py (fastapi, django), so the upper
    # bound is num_python_frameworks * cap. The win is that we did NOT
    # read all 10 candidate files — the cap held.
    assert len(reads) < 10
    # Per-pattern reads are still bounded by the cap.
    py_reads = [p for p in reads if p.suffix == ".py"]
    # 2 frameworks × 3-cap = 6 .py reads max.
    assert len(py_reads) <= 6


# ── load_skill ──────────────────────────────────────────────────────────────

def test_available_frameworks_matches_marker_table():
    """AVAILABLE_FRAMEWORKS exposes exactly the frameworks the detector knows."""
    assert set(AVAILABLE_FRAMEWORKS) == {
        "spring-boot", "fastapi", "nestjs", "django", "rails", "nextjs",
    }


def test_load_skill_unknown_framework_returns_empty():
    """An unknown framework name yields "" — never a KeyError."""
    assert load_skill("not-a-real-framework") == ""


@pytest.mark.parametrize("framework", AVAILABLE_FRAMEWORKS)
def test_load_skill_returns_nonempty_for_each_supported_framework(framework: str):
    """Every supported framework ships a non-trivial SKILL.md.

    Sanity-checks both that the file exists at the expected path and that
    it has enough content to be useful (rules out an empty placeholder).
    """
    skill = load_skill(framework)
    assert skill, f"{framework}/SKILL.md is missing or empty"
    # Every shipped skill teaches something — substring check keeps
    # the assertion robust to wording changes.
    assert len(skill) > 500, f"{framework}/SKILL.md is suspiciously short"


def test_load_skill_for_spring_boot_mentions_jpa_and_jooq():
    """Smoke-test the spring-boot SKILL.md content surface."""
    skill = load_skill("spring-boot")
    assert "@RestController" in skill
    assert "@Service" in skill
    assert "JPA" in skill or "@Repository" in skill
