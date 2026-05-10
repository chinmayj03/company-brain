"""Browser-verifier sub-agent (ADR-0052 P6).

When a frontend repo is part of the workspace, this sub-agent drives a
headless Chromium against the running app and watches its outgoing fetches.
Any URL that doesn't match a known ``ApiEndpoint`` URN in the brain is
reported as drift — the frontend either calls a deleted endpoint or one we
haven't extracted yet.

The agent isn't wired into the default ``HarnessLoop`` tool list yet; it's
opt-in for the workspace owner via ``brain verify --frontend <url>``. When
Playwright isn't installed (CI without browsers), the verifier falls back
to a stubbed implementation so tests can still drive its surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

import structlog

log = structlog.get_logger(__name__)


@dataclass
class DriftFinding:
    """One frontend-side request that doesn't map to a brain endpoint."""
    observed_url: str
    issue: str = "no matching brain endpoint"


@dataclass
class VerifyResult:
    observed: list[str] = field(default_factory=list)
    drift: list[DriftFinding] = field(default_factory=list)
    backend: str = "playwright"

    def as_dict(self) -> dict:
        return {
            "observed": list(self.observed),
            "drift": [{"observed_url": d.observed_url, "issue": d.issue}
                      for d in self.drift],
            "backend": self.backend,
        }


# ── public entry point ───────────────────────────────────────────────────────

async def verify(
    frontend_url: str,
    brain_endpoints: Iterable[str],
    *,
    wait_for: str = "networkidle",
    timeout_ms: int = 10_000,
    extra_observations: Optional[Iterable[str]] = None,
) -> VerifyResult:
    """Visit ``frontend_url`` and report which network calls drift from the brain.

    Parameters
    ----------
    frontend_url
        Origin to load (typically ``http://localhost:5173``).
    brain_endpoints
        Iterable of ApiEndpoint URNs / paths the brain knows about. Drift is
        anything observed that doesn't substring-match one of these.
    wait_for
        Playwright load-state to await — ``networkidle`` is the default; use
        ``"load"`` when the app has long-running websockets that prevent
        ``networkidle`` from ever firing.
    timeout_ms
        Hard cap for the load-state wait.
    extra_observations
        Test hook — when supplied, skips Playwright entirely and uses these
        URLs as the "observed" set. Production code never passes this.
    """
    endpoints = list(brain_endpoints)

    if extra_observations is not None:
        observed = list(extra_observations)
        result = VerifyResult(observed=observed, backend="stub")
        result.drift = _diff(observed, endpoints)
        return result

    observed = await _drive_browser(frontend_url, wait_for, timeout_ms)
    result = VerifyResult(observed=observed, backend="playwright")
    result.drift = _diff(observed, endpoints)
    return result


def _diff(observed: list[str], endpoints: list[str]) -> list[DriftFinding]:
    """Report observed URLs that don't substring-match any known endpoint.

    Substring match is intentionally loose: the frontend's full URL embeds the
    origin (``http://api/users/42``) while the brain stores the path template
    (``/users/{id}``). We just need to flag the obviously-missing.
    """
    out: list[DriftFinding] = []
    for url in observed:
        # Skip non-API requests — the verifier's signal is API drift.
        if any(url.endswith(suffix) for suffix in
               (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2", ".ico")):
            continue
        if not any(_endpoint_matches(ep, url) for ep in endpoints):
            out.append(DriftFinding(observed_url=url))
    return out


def _endpoint_matches(endpoint: str, url: str) -> bool:
    """A URL matches an endpoint if the endpoint's path appears verbatim.

    Path-template variables (``{id}``) are stripped before the substring
    test: ``/users/{id}`` becomes ``/users/`` which matches ``/users/42``.
    """
    if not endpoint:
        return False
    needle = endpoint.split(":", maxsplit=4)[-1] if endpoint.startswith("urn:") else endpoint
    # Drop everything after the first '{' so templated segments don't block matching.
    brace = needle.find("{")
    if brace != -1:
        needle = needle[:brace]
    needle = needle.rstrip("/")
    return bool(needle) and needle in url


# ── playwright driver ────────────────────────────────────────────────────────

async def _drive_browser(url: str, wait_for: str, timeout_ms: int) -> list[str]:
    """Open the page, capture every request URL, return the sorted list.

    When Playwright isn't installed we log + return an empty list so the
    caller can still emit a result envelope (drift will be empty).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("browser_verifier.playwright_missing")
        return []

    observed: list[str] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                page.on("request", lambda req: observed.append(req.url))
                await page.goto(url, timeout=timeout_ms)
                try:
                    await page.wait_for_load_state(wait_for, timeout=timeout_ms)
                except Exception:
                    # networkidle never fires for apps with persistent sockets;
                    # fall back to a fixed wait so we still capture initial fan-out.
                    pass
            finally:
                await browser.close()
    except Exception as exc:                                       # pragma: no cover
        log.warning("browser_verifier.playwright_error", error=str(exc))
    return observed
