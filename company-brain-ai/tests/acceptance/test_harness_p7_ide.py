"""Acceptance tests for ADR-0052 Phase 7 — VS Code IDE integration.

Each test asserts one headline P7 property end-to-end:

* the VS Code extension packages cleanly via ``vsce`` (manifest + tsconfig
  are valid, all `src/` files compile);
* the bundled headless MCP client (``test/headless-query.js``) can call the
  harness MCP server and parse a structured response.

The tests are intentionally tolerant about Node/npm availability — if the
host doesn't have them installed, the tests skip rather than fail. CI
(``.github/workflows/vscode-extension.yml``) runs the same packaging step on
every PR, so the no-Node-locally case is covered.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from companybrain.harness.mcp_server import build_server
from companybrain.store.base import BrainEntity
from companybrain.store.json_store import JsonFileBrainStore

REPO_ROOT = Path(__file__).resolve().parents[3]
EXT_DIR = REPO_ROOT / "ide" / "vscode-extension"


# ── 1. The VS Code extension packages cleanly ─────────────────────────────


def test_vscode_extension_packages_cleanly(tmp_path: Path):
    """`npm install && vsce package` produces a .vsix without error.

    Slow (~30s for npm install). Opt-in via COMPANYBRAIN_RUN_VSCE=1 so a
    `pytest tests/acceptance/` run on a dev laptop stays fast; CI sets the
    env var so the GitHub workflow exercises the full path.
    """
    if not os.environ.get("COMPANYBRAIN_RUN_VSCE"):
        pytest.skip("set COMPANYBRAIN_RUN_VSCE=1 to run npm/vsce packaging (~30s)")
    if shutil.which("npm") is None:
        pytest.skip("npm not installed on this host")

    # Run install + package against the actual ide/vscode-extension dir; vsce
    # writes the .vsix into the working directory, so we copy the dir into a
    # tmp path first to keep the source tree clean.
    work = tmp_path / "vscode-extension"
    shutil.copytree(EXT_DIR, work)

    # Strip any stale build artefacts.
    for stale in (work / "node_modules", work / "out"):
        if stale.exists():
            shutil.rmtree(stale)
    for vsix in work.glob("*.vsix"):
        vsix.unlink()

    install = subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund", "--silent"],
        cwd=work, capture_output=True, text=True, timeout=300,
    )
    assert install.returncode == 0, install.stderr or install.stdout

    compile_proc = subprocess.run(
        ["npm", "run", "compile"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )
    assert compile_proc.returncode == 0, compile_proc.stderr or compile_proc.stdout
    assert (work / "out" / "extension.js").exists(), "tsc did not emit out/extension.js"

    package_proc = subprocess.run(
        ["npx", "--yes", "@vscode/vsce", "package", "--no-yarn", "--no-dependencies"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )
    assert package_proc.returncode == 0, package_proc.stderr or package_proc.stdout
    vsix = list(work.glob("*.vsix"))
    assert vsix, "no .vsix produced"


# ── 2. The headless MCP client speaks to a live server ────────────────────


async def test_extension_brain_client_calls_mcp_server(tmp_path: Path):
    """Headless: the extension's wire format gets a structured payload back."""
    if shutil.which("node") is None:
        pytest.skip("node not installed on this host")

    # Seed a deterministic .brain/ store so query_brain has something to
    # match — we hit the same fixture path the P5 acceptance test uses.
    repo = tmp_path / "repo"
    repo.mkdir()
    store = JsonFileBrainStore(repo / ".brain")
    await store.write(
        BrainEntity(
            id="urn:cb:dev:code:demo:method:Foo.bar",
            entity_type="function_node",
            repo="demo",
            file="src/Foo.java",
            qualified_name="Foo.bar",
            t1_summary="Looks up competitive_payer_plan rows for getPayerCompetitors.",
        ),
        run_id="r1",
        workspace_id="ws-test",
    )

    # Spin up the MCP server on a free port using uvicorn in-process.
    import uvicorn

    server = build_server(workspace_id="ws-test", brain_root=repo)
    app = server.build_asgi_app()
    port = _pick_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    uv_server = uvicorn.Server(config)

    serve_task = asyncio.create_task(uv_server.serve())
    try:
        await _wait_until_listening("127.0.0.1", port, timeout=10.0)

        # Use asyncio.create_subprocess_exec — the synchronous subprocess.run
        # would block the event loop and starve uvicorn from accepting the
        # node client's connection.
        node_proc = await asyncio.create_subprocess_exec(
            "node", str(EXT_DIR / "test" / "headless-query.js"),
            env={
                **os.environ,
                "MCP_URL":  f"http://127.0.0.1:{port}",
                "QUESTION": "what does Foo.bar do?",
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                node_proc.communicate(), timeout=15.0,
            )
        except asyncio.TimeoutError:
            node_proc.kill()
            await node_proc.communicate()
            raise
    finally:
        uv_server.should_exit = True
        try:
            await asyncio.wait_for(serve_task, timeout=5.0)
        except asyncio.TimeoutError:
            serve_task.cancel()

    stdout = stdout_b.decode()
    stderr = stderr_b.decode()
    assert node_proc.returncode == 0, stderr or stdout
    payload = json.loads(stdout.strip().splitlines()[-1])
    assert "summary_md" in payload
    assert payload.get("question") == "what does Foo.bar do?"
    assert isinstance(payload.get("matches"), list)
    assert any(
        "competitive_payer_plan" in (m.get("summary") or "")
        for m in payload["matches"]
    ), payload


# ── 3. Manifest invariants ────────────────────────────────────────────────


def test_vscode_manifest_declares_required_commands_and_views():
    """package.json contributes the four headline P7 commands and the brain view."""
    manifest = json.loads((EXT_DIR / "package.json").read_text())
    contributes = manifest["contributes"]
    command_ids = {c["command"] for c in contributes["commands"]}
    assert {
        "companyBrain.askBrain",
        "companyBrain.openSidebar",
        "companyBrain.refreshContext",
        "companyBrain.extractCurrentEndpoint",
    }.issubset(command_ids)

    view_ids = {v["id"] for v in contributes["views"]["companyBrain"]}
    assert "companyBrain.context" in view_ids

    cfg = contributes["configuration"]["properties"]
    assert "companyBrain.mcpUrl" in cfg
    assert cfg["companyBrain.mcpUrl"]["default"] == "http://localhost:8765"


def test_jetbrains_skeleton_present():
    """JetBrains plugin scaffold ships in this PR but stays a skeleton."""
    plugin_dir = REPO_ROOT / "ide" / "jetbrains-plugin"
    assert (plugin_dir / "build.gradle.kts").is_file()
    assert (plugin_dir / "src" / "main" / "resources" / "META-INF" / "plugin.xml").is_file()
    actions_kt = plugin_dir / "src" / "main" / "kotlin" / "com" / "companybrain" / "brain" / "AskBrainAction.kt"
    assert actions_kt.is_file()
    # Skeleton must surface the placeholder copy so reviewers can tell the
    # plugin doesn't claim to do real work yet.
    assert "coming soon" in actions_kt.read_text().lower()


# ── helpers ────────────────────────────────────────────────────────────────


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_until_listening(host: str, port: int, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except OSError as exc:
            last_err = exc
            await asyncio.sleep(0.05)
            continue
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return
    raise TimeoutError(f"server on {host}:{port} did not start in {timeout}s ({last_err})")


