"""Plugin marketplace — install / list / discover (ADR-0052 P6).

A plugin is a zip bundle that ships:

    plugin.json      manifest (name, version, required_capabilities, ...)
    skills/          framework-specific SKILL.md files (override bundled ones)
    hooks/           hook scripts referenced from settings.local.json
    commands/        slash commands consumed by harness/commands/
    tools/           extra tool definitions (advisory; not auto-loaded today)

Bundles install to ``~/.brain/plugins/<name>/`` so the user's shell environment
controls which plugins are active. The acme-spring-boot fixture in
``fixtures/plugins/`` is the canonical reference.

Public surface
--------------

* ``install(source)`` — extract a local .zip / URL / known name into PLUGIN_HOME.
* ``list_installed()`` — every plugin currently on disk.
* ``discover_skills()`` — ``{framework_name: SKILL.md path}`` for
  ``harness.skills`` to consult before falling back to the bundled tree.

Security note: this loader extracts arbitrary zip archives. We reject any
member whose normalised path escapes the plugin directory; the caller is
responsible for trusting the source URL.
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlretrieve

import structlog

log = structlog.get_logger(__name__)


def _plugin_home() -> Path:
    """Resolve $BRAIN_PLUGIN_HOME or fall back to ~/.brain/plugins.

    Resolved at call time, not import time, so tests can override the env var.
    """
    override = os.environ.get("BRAIN_PLUGIN_HOME")
    if override:
        return Path(override)
    return Path.home() / ".brain" / "plugins"


# Re-exported so callers can ``from plugins import PLUGIN_HOME`` for diagnostics.
PLUGIN_HOME = _plugin_home()


@dataclass
class Plugin:
    """One installed plugin. ``root`` is the on-disk extraction directory."""
    name: str
    version: str
    capabilities: list[str]
    root: Path

    @classmethod
    def from_manifest(cls, manifest: dict, root: Path) -> "Plugin":
        return cls(
            name=str(manifest["name"]),
            version=str(manifest.get("version", "0.0.0")),
            capabilities=list(manifest.get("required_capabilities", [])),
            root=root,
        )


# ── install ──────────────────────────────────────────────────────────────────

def install(source: str) -> Plugin:
    """Install a plugin from a local .zip path or a URL.

    A bare name (``"acme-spring-boot"``) is left as-is and treated as a local
    path; we don't ship a registry yet, so name-only installs only work when
    the caller is ``brain plugin install ./bundle.zip``.

    Returns the parsed :class:`Plugin`. Raises if the manifest is missing.
    """
    home = _plugin_home()
    home.mkdir(parents=True, exist_ok=True)

    local_zip: Path
    if source.startswith(("http://", "https://")):
        local_zip = home / Path(source).name
        log.info("plugins.install.fetch", source=source, dest=str(local_zip))
        urlretrieve(source, str(local_zip))   # noqa: S310 — caller-trusted URL
    else:
        local_zip = Path(source)

    if not local_zip.is_file():
        raise FileNotFoundError(f"plugin bundle not found: {local_zip}")

    with zipfile.ZipFile(local_zip) as zf:
        if "plugin.json" not in zf.namelist():
            raise ValueError(f"plugin bundle missing plugin.json: {local_zip}")
        manifest = json.loads(zf.read("plugin.json"))
        name = manifest["name"]
        target = home / name

        # Refuse archive members that resolve outside `target` once joined —
        # the standard CVE-2007-4559 zip-slip mitigation.
        for member in zf.infolist():
            dest = (target / member.filename).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise ValueError(f"unsafe path in plugin bundle: {member.filename!r}")

        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        zf.extractall(target)

    log.info("plugins.install.ok", name=name, version=manifest.get("version"),
             root=str(target))
    return Plugin.from_manifest(manifest, target)


# ── inventory ────────────────────────────────────────────────────────────────

def list_installed() -> list[Plugin]:
    """Every directory in PLUGIN_HOME that contains a valid plugin.json."""
    home = _plugin_home()
    if not home.exists():
        return []
    out: list[Plugin] = []
    for d in sorted(home.iterdir()):
        if not d.is_dir():
            continue
        manifest_path = d / "plugin.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("plugins.list.bad_manifest", dir=str(d), error=str(exc))
            continue
        out.append(Plugin.from_manifest(manifest, d))
    return out


def uninstall(name: str) -> bool:
    """Remove the plugin directory. Returns True iff something was deleted."""
    target = _plugin_home() / name
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    log.info("plugins.uninstall.ok", name=name)
    return True


# ── skill discovery ──────────────────────────────────────────────────────────

def discover_skills() -> dict[str, Path]:
    """Map ``framework_name → SKILL.md path`` from every installed plugin.

    Plugins extend the framework skill catalogue. When two plugins ship the
    same framework name the later one (alphabetical) wins; we don't yet have
    a precedence policy beyond that, but :func:`harness.skills.load_skill`
    only ever calls this lookup with one name at a time.

    The framework name is taken from the SKILL.md's parent directory, so a
    plugin can ship multiple skills under ``skills/<framework>/SKILL.md``.
    """
    out: dict[str, Path] = {}
    for plugin in list_installed():
        skills_dir = plugin.root / "skills"
        if not skills_dir.is_dir():
            continue
        for skill_md in skills_dir.glob("**/SKILL.md"):
            framework = skill_md.parent.name
            out[framework] = skill_md
    return out
