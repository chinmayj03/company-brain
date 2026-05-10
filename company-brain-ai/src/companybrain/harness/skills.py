"""Skill detection + loading (ADR-0051 P3).

The harness loads at most one framework-specific SKILL.md into the system
prompt per run. The skill is a focused ~2 KB markdown file that teaches the
agent the conventions, annotations, and false-positives of one framework
(Spring Boot, FastAPI, NestJS, Django, Rails, Next.js).

Picking the right skill is a cheap deterministic scan: count file-marker hits
per framework, return the framework with the most hits. The scan is capped at
50 files per pattern to keep it under ~50 ms even on large repos.

Public surface:
    detect_framework(repo_path) -> Optional[str]
    load_skill(framework)       -> str
    AVAILABLE_FRAMEWORKS        -> tuple[str, ...]
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


# (glob pattern, predicate) per framework. The predicate runs against the
# file's text contents; a True return adds one to that framework's score.
_FRAMEWORK_MARKERS: dict[str, list[tuple[str, Callable[[str], bool]]]] = {
    "spring-boot": [
        ("**/*.java",     lambda t: "@SpringBootApplication" in t or "spring-boot-starter" in t),
        ("pom.xml",       lambda t: "spring-boot-starter" in t),
        ("build.gradle",  lambda t: "spring-boot" in t),
        ("build.gradle.kts", lambda t: "spring-boot" in t),
    ],
    "fastapi": [
        ("**/*.py",          lambda t: "from fastapi import" in t or "import fastapi" in t),
        ("pyproject.toml",   lambda t: "fastapi" in t.lower()),
        ("requirements.txt", lambda t: "fastapi" in t.lower()),
    ],
    "nestjs": [
        ("**/*.ts",     lambda t: "@nestjs/core" in t or "@nestjs/common" in t),
        ("package.json", lambda t: '"@nestjs/' in t),
    ],
    "django": [
        ("**/*.py", lambda t: "from django" in t or "import django" in t),
        ("manage.py", lambda t: "django" in t.lower()),
    ],
    "rails": [
        ("Gemfile",      lambda t: "rails" in t.lower()),
        ("**/routes.rb", lambda t: "Rails.application.routes" in t),
    ],
    "nextjs": [
        ("package.json", lambda t: '"next":' in t or '"next/' in t),
        ("next.config.js", lambda _t: True),
        ("next.config.mjs", lambda _t: True),
    ],
}

AVAILABLE_FRAMEWORKS: tuple[str, ...] = tuple(_FRAMEWORK_MARKERS.keys())

# frameworks/ lives next to src/ inside the company-brain-ai package root:
#   company-brain-ai/
#     ├─ src/companybrain/harness/skills.py     ← __file__
#     └─ frameworks/<name>/SKILL.md
_FRAMEWORKS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frameworks"

# Cap per-pattern scan to keep detection cheap on big monorepos. A real Spring
# repo trips the marker on the first .java that imports the starter; we don't
# need to read the other 10 000.
_MAX_FILES_PER_PATTERN = 50

# Skip well-known noise directories during the scan. rglob doesn't honour
# .gitignore, so a 200-MB node_modules would otherwise dominate the budget.
_SKIP_DIR_NAMES = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "target", "build", "dist", ".gradle", ".idea", ".vscode",
})


def detect_framework(repo_path: Path | str) -> str | None:
    """Return the framework with the most marker hits, or None.

    Scans up to `_MAX_FILES_PER_PATTERN` files per (framework, pattern) pair
    and tallies how many files match the predicate. The framework with the
    highest score wins. A repo with no recognised markers returns None and
    no skill is loaded.
    """
    root = Path(repo_path)
    if not root.exists() or not root.is_dir():
        return None

    scores: Counter[str] = Counter()
    for fw, patterns in _FRAMEWORK_MARKERS.items():
        for pattern, predicate in patterns:
            for f in _scan(root, pattern, _MAX_FILES_PER_PATTERN):
                try:
                    if predicate(f.read_text(errors="ignore")):
                        scores[fw] += 1
                except OSError:
                    # Permission denied / broken symlink / racing rm — skip.
                    continue

    if not scores:
        log.debug("skills.detect.no_match", repo_path=str(root))
        return None

    fw, score = scores.most_common(1)[0]
    log.debug("skills.detect.match", framework=fw, score=score, all_scores=dict(scores))
    return fw


def load_skill(framework: str) -> str:
    """Read the SKILL.md for `framework`. Returns "" if it's not registered.

    The harness injects the returned text under a `# Framework Skill: <name>`
    heading, so the file should not lead with its own H1.

    ADR-0052 P6: when an installed plugin ships a SKILL.md for the same
    framework name, the plugin version takes precedence over the bundled tree.
    This is how teams override our built-in spring-boot skill with their own
    house-style ``acme-spring-boot``.
    """
    # Plugin-supplied skill wins. Imported locally so the cheap-detect path
    # never pays for the plugin scan when no skill is being loaded.
    try:
        from companybrain.harness import plugins as _plugins
        plugin_skills = _plugins.discover_skills()
    except Exception as exc:                  # pragma: no cover — diagnostic
        log.debug("skills.load.plugin_discovery_failed", error=str(exc))
        plugin_skills = {}
    if framework in plugin_skills:
        try:
            text = plugin_skills[framework].read_text()
            log.info("skills.load.from_plugin",
                     framework=framework, path=str(plugin_skills[framework]))
            return text
        except OSError as exc:
            log.warning("skills.load.plugin_read_error",
                        framework=framework, error=str(exc))

    if framework not in _FRAMEWORK_MARKERS:
        return ""
    skill = _FRAMEWORKS_DIR / framework / "SKILL.md"
    if not skill.exists():
        return ""
    try:
        return skill.read_text()
    except OSError as exc:
        log.warning("skills.load.error", framework=framework, error=str(exc))
        return ""


def _scan(root: Path, pattern: str, limit: int) -> list[Path]:
    """rglob with a hard cap and a skip-list for well-known noise dirs."""
    out: list[Path] = []
    for p in root.rglob(pattern):
        if any(part in _SKIP_DIR_NAMES for part in p.parts):
            continue
        if not p.is_file():
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out
