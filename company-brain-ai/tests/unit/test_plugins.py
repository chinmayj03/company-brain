"""Unit tests for the plugin marketplace (ADR-0052 P6).

These tests build small zip bundles in tmp_path, install them into a
temp PLUGIN_HOME, and assert that the discovered surface lines up with
what we wrote: manifest fields parsed correctly, skills resolvable by
framework name, install/uninstall round-trip clean, zip-slip rejected.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from companybrain.harness import plugins as plugins_mod


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_bundle(
    tmp_path: Path,
    name: str = "demo-plugin",
    version: str = "0.0.1",
    skills: dict[str, str] | None = None,
    capabilities: list[str] | None = None,
    extra_members: list[tuple[str, str]] | None = None,
) -> Path:
    """Build a .zip bundle on disk, return the path."""
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": version,
        "required_capabilities": capabilities or [],
    }
    (src / "plugin.json").write_text(json.dumps(manifest))
    if skills:
        for fw, body in skills.items():
            d = src / "skills" / fw
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(body)
    bundle = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src))
        for member_name, content in (extra_members or []):
            zf.writestr(member_name, content)
    return bundle


@pytest.fixture
def plugin_home(tmp_path, monkeypatch):
    """Pin PLUGIN_HOME to tmp_path so installs don't pollute ~/.brain/."""
    home = tmp_path / "plugin_home"
    monkeypatch.setenv("BRAIN_PLUGIN_HOME", str(home))
    return home


# ── install ──────────────────────────────────────────────────────────────────

def test_install_extracts_local_zip(tmp_path: Path, plugin_home: Path):
    """A vanilla bundle installs to <PLUGIN_HOME>/<name>/ and parses the manifest."""
    bundle = _make_bundle(
        tmp_path, name="demo", version="1.2.3",
        skills={"fastapi": "# acme fastapi\n"},
        capabilities=["read_code"],
    )

    plugin = plugins_mod.install(str(bundle))

    assert plugin.name == "demo"
    assert plugin.version == "1.2.3"
    assert plugin.capabilities == ["read_code"]
    assert plugin.root == plugin_home / "demo"
    assert (plugin.root / "plugin.json").is_file()
    assert (plugin.root / "skills" / "fastapi" / "SKILL.md").read_text() == "# acme fastapi\n"


def test_install_rejects_missing_manifest(tmp_path: Path, plugin_home: Path):
    """Bundle without plugin.json raises — we don't half-install."""
    bundle = tmp_path / "broken.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("README.md", "no manifest here")

    with pytest.raises(ValueError, match="missing plugin.json"):
        plugins_mod.install(str(bundle))


def test_install_rejects_zip_slip(tmp_path: Path, plugin_home: Path):
    """A bundle whose member path escapes the install dir is rejected."""
    bundle = _make_bundle(
        tmp_path, name="evil",
        extra_members=[("../../escape.txt", "pwned")],
    )

    with pytest.raises(ValueError, match="unsafe path"):
        plugins_mod.install(str(bundle))

    # And nothing should have been extracted to the home dir.
    assert not (plugin_home / "evil").exists()


def test_install_replaces_existing_plugin(tmp_path: Path, plugin_home: Path):
    """Installing the same name twice overwrites the previous extraction."""
    b1 = _make_bundle(tmp_path / "a", name="dup", version="0.1.0")
    b2 = _make_bundle(tmp_path / "b", name="dup", version="0.2.0")

    plugins_mod.install(str(b1))
    p2 = plugins_mod.install(str(b2))

    assert p2.version == "0.2.0"
    manifest = json.loads((p2.root / "plugin.json").read_text())
    assert manifest["version"] == "0.2.0"


# ── inventory ────────────────────────────────────────────────────────────────

def test_list_installed_returns_each_plugin(tmp_path: Path, plugin_home: Path):
    plugins_mod.install(str(_make_bundle(tmp_path / "a", name="alpha")))
    plugins_mod.install(str(_make_bundle(tmp_path / "b", name="bravo")))

    names = sorted(p.name for p in plugins_mod.list_installed())
    assert names == ["alpha", "bravo"]


def test_list_installed_skips_dirs_without_manifest(tmp_path: Path, plugin_home: Path):
    """A stray directory under PLUGIN_HOME is ignored, not crashed-on."""
    plugins_mod.install(str(_make_bundle(tmp_path, name="real")))
    (plugin_home / "stray").mkdir()
    (plugin_home / "stray" / "README").write_text("not a plugin")

    names = [p.name for p in plugins_mod.list_installed()]
    assert names == ["real"]


def test_uninstall_removes_directory(tmp_path: Path, plugin_home: Path):
    plugins_mod.install(str(_make_bundle(tmp_path, name="goner")))

    assert plugins_mod.uninstall("goner") is True
    assert not (plugin_home / "goner").exists()
    assert plugins_mod.uninstall("goner") is False  # idempotent


# ── skill discovery ──────────────────────────────────────────────────────────

def test_discover_skills_maps_framework_to_path(tmp_path: Path, plugin_home: Path):
    bundle = _make_bundle(
        tmp_path, name="multi",
        skills={
            "spring-boot": "# acme spring-boot\n",
            "fastapi":     "# acme fastapi\n",
        },
    )
    plugins_mod.install(str(bundle))

    discovered = plugins_mod.discover_skills()

    assert set(discovered.keys()) == {"spring-boot", "fastapi"}
    assert discovered["spring-boot"].read_text() == "# acme spring-boot\n"


def test_discover_skills_empty_when_nothing_installed(plugin_home: Path):
    assert plugins_mod.discover_skills() == {}


# ── integration with skills.load_skill ───────────────────────────────────────

def test_plugin_skill_overrides_bundled_when_installed(
    tmp_path: Path, plugin_home: Path,
):
    """A plugin shipping a spring-boot SKILL.md beats the bundled tree.

    Mirrors the acceptance test's intent without spinning up the harness.
    """
    bundle = _make_bundle(
        tmp_path, name="acme-spring",
        skills={"spring-boot": "# Acme override\nuse @AcmeAuditable\n"},
    )
    plugins_mod.install(str(bundle))

    from companybrain.harness import skills

    text = skills.load_skill("spring-boot")
    assert "Acme override" in text
    assert "@AcmeAuditable" in text
