#!/usr/bin/env python3
"""Demo verification gates — ADR-0054.

Runs 5 gates that prove the brain is demo-ready.  All gates run concurrently
and must complete in < 30 s total.

Exit codes:
    0  all gates passed
    1  at least one gate failed
"""
from __future__ import annotations

import concurrent.futures
import glob
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN = "\033[32m"
RED   = "\033[31m"
CYAN  = "\033[36m"
NC    = "\033[0m"

# ── Config (mirrors Makefile.demo defaults) ───────────────────────────────────
AI_PORT     = int(os.environ.get("AI_PORT",     "8000"))
JAVA_PORT   = int(os.environ.get("JAVA_PORT",   "8080"))
WORKSPACE   = os.environ.get("WORKSPACE_ID",    "00000000-0000-0000-0000-000000000001")
TARGET_REPO = os.environ.get("TARGET_REPO",     "/Users/chinmayjadhav/Documents/network-iq-backend-java")
TIMEOUT_FAST  = 10   # seconds — health checks
TIMEOUT_QUERY = 28   # seconds — LLM-backed query (measured ~17 s in prod)

QUERY_QUESTION = "what tables have a lobName column"


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str
    fix: str = ""
    counts: dict = field(default_factory=dict)


# ── Gate implementations ───────────────────────────────────────────────────────

def _http_get(url: str, timeout: int = TIMEOUT_FAST) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _http_post(url: str, body: dict, timeout: int = TIMEOUT_FAST) -> dict:
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data,
                                   headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def gate_ai_health() -> GateResult:
    """G1: Python AI service responds healthy."""
    name = "AI health (localhost:{})".format(AI_PORT)
    try:
        data = _http_get(f"http://localhost:{AI_PORT}/health")
        if data.get("status") == "ok":
            return GateResult(name, True,
                              f"status=ok  provider={data.get('llm_provider','?')}")
        return GateResult(name, False,
                          f"unexpected body: {data}",
                          f"Run 'make ai' in a separate terminal and wait for it to print 'started'")
    except Exception as exc:
        return GateResult(name, False, str(exc),
                          f"Start the AI service: cd company-brain-ai && .venv/bin/uvicorn src.main:app --port {AI_PORT}")


def gate_java_health() -> GateResult:
    """G2: Java Spring Boot actuator is up."""
    name = "Java backend health (localhost:{})".format(JAVA_PORT)
    try:
        data = _http_get(f"http://localhost:{JAVA_PORT}/actuator/health")
        if data.get("status") == "UP":
            return GateResult(name, True, "status=UP")
        return GateResult(name, False,
                          f"actuator returned: {data}",
                          "Check 'make backend' — wait for 'Started Application'")
    except Exception as exc:
        return GateResult(name, False, str(exc),
                          "Run 'make backend' in a separate terminal and wait for startup")


def gate_pipeline_smoke() -> GateResult:
    """G3: At least 1 file was extracted into .brain."""
    name = "Pipeline smoke (≥ 1 entity extracted)"
    brain_dirs = [
        os.path.join(TARGET_REPO, ".brain", "component"),
        os.path.join(TARGET_REPO, ".brain", "function_node"),
        os.path.join(TARGET_REPO, ".brain", "data_model"),
    ]
    counts: dict[str, int] = {}
    for d in brain_dirs:
        label = os.path.basename(d)
        files = glob.glob(os.path.join(d, "*.json"))
        counts[label] = len(files)

    total = sum(counts.values())
    if total >= 1:
        detail = "  ".join(f"{k}={v}" for k, v in counts.items() if v)
        return GateResult(name, True, detail, counts=counts)
    return GateResult(name, False,
                      f".brain has no entities (checked: {', '.join(brain_dirs)})",
                      "Run an extraction first: make -f Makefile.demo run-cli ENDPOINT=/... METHOD=POST")


def gate_query_smoke() -> GateResult:
    """G4: Query for 'lobName' returns ≥ 1 affected entity."""
    name = f"Query smoke ('{QUERY_QUESTION}' → ≥ 1 entity)"
    try:
        data = _http_post(
            f"http://localhost:{AI_PORT}/query",
            {"question": QUERY_QUESTION,
             "workspace_id": WORKSPACE,
             "repo_path": TARGET_REPO},
            timeout=TIMEOUT_QUERY,
        )
        entities = data.get("affected_entities", [])
        n = len(entities)
        if n >= 1:
            top = entities[0].get("name", "?")
            return GateResult(name, True,
                              f"{n} entity(ies) returned  top={top}")
        return GateResult(name, False,
                          "affected_entities is empty — brain may not be indexed",
                          "Run an extraction with 'make -f Makefile.demo run-cli' then retry")
    except Exception as exc:
        return GateResult(name, False, str(exc),
                          f"Ensure AI service is up (G1) and try: make -f Makefile.demo ask Q='{QUERY_QUESTION}'")


def gate_citation_verify() -> GateResult:
    """G5: At least 1 entity points to a real source file on disk."""
    name = "Citation verify (≥ 1 real file on disk)"
    brain_root = os.path.join(TARGET_REPO, ".brain")
    found: list[str] = []
    checked = 0
    for pattern in ("component/*.json", "function_node/*.json", "data_model/*.json"):
        for fpath in glob.glob(os.path.join(brain_root, pattern)):
            checked += 1
            try:
                with open(fpath) as f:
                    entity = json.load(f)
                src = entity.get("file", "")
                if src and os.path.isfile(src):
                    found.append(src)
                    if len(found) >= 3:  # enough evidence — stop early
                        break
            except (json.JSONDecodeError, OSError):
                continue
        if found:
            break

    if found:
        rel = os.path.relpath(found[0], TARGET_REPO)
        return GateResult(name, True,
                          f"{len(found)} real file(s) verified  e.g. {rel}  (checked {checked} entities)")
    return GateResult(name, False,
                      f"No entity's 'file' field points to a real path (checked {checked} entities)",
                      "Re-run extraction against the correct TARGET_REPO path; current: " + TARGET_REPO)


# ── Runner ────────────────────────────────────────────────────────────────────

GATES = [
    gate_ai_health,
    gate_java_health,
    gate_pipeline_smoke,
    gate_query_smoke,
    gate_citation_verify,
]


def main() -> int:
    print(f"{CYAN}→ Demo verification gates (ADR-0054){NC}")
    print(f"  repo:      {TARGET_REPO}")
    print(f"  workspace: {WORKSPACE}")
    print()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(GATES)) as pool:
        futures = {pool.submit(g): g.__name__ for g in GATES}
        results: list[GateResult] = []
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                fn = futures[future]
                results.append(GateResult(fn, False, f"gate raised: {exc}",
                                          "debug the gate runner itself"))

    # Re-sort to match GATES order for stable output
    order = {g.__name__: i for i, g in enumerate(GATES)}
    results.sort(key=lambda r: order.get(r.name.split(" ")[0], 99))

    all_pass = True
    for r in results:
        if r.passed:
            print(f"  {GREEN}✓{NC} {r.name}")
            print(f"      {r.detail}")
        else:
            all_pass = False
            print(f"  {RED}✗{NC} {r.name}")
            print(f"      {r.detail}")
            if r.fix:
                print(f"      {RED}→ {r.fix}{NC}")

    print()
    if all_pass:
        # Print entity counts summary from gate 3
        g3 = next((r for r in results if "Pipeline smoke" in r.name), None)
        counts_str = ""
        if g3 and g3.counts:
            counts_str = "  entities: " + "  ".join(f"{k}={v}" for k, v in g3.counts.items())
        print(f"{GREEN}✓ all gates passed — brain is demo-ready{NC}{counts_str}")
        return 0
    else:
        failed = sum(1 for r in results if not r.passed)
        print(f"{RED}✗ {failed} gate(s) failed — fix above before demo{NC}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
