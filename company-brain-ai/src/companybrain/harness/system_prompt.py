"""System prompt for the HarnessLoop (ADR-0051 P1, extended in P3).

The prompt is generated from the live tool registry so a newly registered tool
is announced to the model on the next run with no manual edit to this file.

Phase 3 additions:
  • Per-framework SKILL.md (companybrain.harness.skills) appended when the repo
    matches a known framework (Spring Boot, FastAPI, NestJS, Django, Rails,
    Next.js). Detection mutates the passed `context` dict with `skill_loaded`
    so HarnessLoop can surface it in telemetry.
  • Per-repo BRAIN.md (companybrain.harness.memory) appended verbatim when the
    repo has one. The agent reads curated gotchas + the pipeline's auto-
    appended observations.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

_PROMPT_TEMPLATE = """\
You are the company-brain extraction agent.

Goal
----
Extract the entities, relationships, and business context for an HTTP endpoint's
call chain into the persistent brain store. The user message names the endpoint
(method + path) and the repository path.

Canonical pipeline
------------------
This is the legacy linear pipeline expressed as tool calls. Follow it unless
you have a concrete reason to deviate. If you deviate, state the reason in a
short text turn before the tool call so the trace is debuggable.

  1. discover_routes(repo_path)
        — confirm the endpoint exists. If not, stop and report the closest
          matches; do not guess.
  2. find_entry_handler(endpoint, http_method, repo_path)
        — identify the controller class + method that handles the route.
  3. list_candidate_files(endpoint, repo_path)
        — get the bounded manifest of files worth extracting (services,
          repositories, DTOs the handler reaches).
  4. For each candidate file:
        extract_methods_from_class(file, methods)
        — runs the batched ContextAgent extractor AND persists the resulting
        entities to the brain store in one step. Returns a compact summary
        {{written, skipped, errors, qnames_written}} — NOT the full entity
        payload. Prefer one batched call per file over many per-method calls.
        You DO NOT need to call write_to_brain afterwards.
  5. (advanced/manual writes only)
        write_to_brain(entities, edges)
        — Only call this if you have entities you constructed yourself (e.g.
        from grep_code observations) that extract_methods_from_class did not
        produce. Normal flow skips this step entirely.
  6. finalize_brain(workspace_id)
        — closes the run and updates the brain manifest. Call exactly once at
        the end, after the last extract_methods_from_class call. As soon as
        you have finished extracting every relevant file, your next tool call
        MUST be finalize_brain.

Then emit a short text summary (one paragraph) of what was extracted and stop.

Stop criteria (call finalize_brain + a text turn — DO NOT keep looping):
  • You have extracted every relevant file in the call chain.
  • extract_methods_from_class has returned {{written: 0, ...}} twice
    consecutively (nothing new to extract — that means you're done).
  • You have already run 6+ extract_methods_from_class calls: stop and call
    finalize_brain even if you think there is more to do. Coverage gaps
    belong in the final text turn, not in another extract or write call.

Available tools
---------------
{tool_list}

Operating guidelines
--------------------
- Batch tool calls in a single assistant turn when the calls are independent
  (the harness fans them out in parallel — one turn of latency for N calls).
- Do not re-read files you have already extracted; read_file is for orientation
  only. extract_methods_from_class already reads the file via the shared cache.
- If a tool returns {{"error": "..."}}, decide whether to retry with different
  arguments, fall back to a different tool, or stop. Do not loop on the same
  failing call.
- Stop calling tools when the extraction is complete. Your final text turn is
  the signal to exit.
- Workspace context is provided implicitly to every tool; do not pass
  workspace_id unless a tool's schema asks for it.

Run context
-----------
{context_block}
"""


def build_system_prompt(context: dict[str, Any]) -> str:
    """Build the system prompt against the current tool registry + run context.

    `context` is the same dict forwarded to tool handlers. Only a few fields
    are echoed into the prompt; everything else stays hidden from the model.
    """
    # Defer import to avoid a registry-vs-prompt import cycle.
    from companybrain.harness.tools import TOOL_REGISTRY

    tool_list = "\n".join(
        f"- {t.name}: {t.description}"
        for t in TOOL_REGISTRY.values()
    ) or "(no tools registered)"

    workspace = context.get("workspace_id", "(unset)")
    repo_path = context.get("repo_path", "(unset)")
    endpoint  = context.get("endpoint_path", "(unset)")
    method    = context.get("http_method", "(unset)")

    context_block = (
        f"workspace_id : {workspace}\n"
        f"repo_path    : {repo_path}\n"
        f"endpoint     : {method} {endpoint}"
    )

    prompt = _PROMPT_TEMPLATE.format(
        tool_list=tool_list,
        context_block=context_block,
    )

    # ── Phase-3 attachments: framework skill + per-repo BRAIN.md ────────────
    # Both are best-effort: a missing repo, missing framework match, or
    # missing BRAIN.md is the common case and contributes nothing.
    repo_path_raw = context.get("repo_path")
    if repo_path_raw:
        repo = Path(repo_path_raw)
        if repo.exists() and repo.is_dir():
            prompt += _build_skill_section(repo, context)
            prompt += _build_memory_section(repo, context)

    return prompt


def _build_skill_section(repo: Path, context: dict[str, Any]) -> str:
    """Detect the framework, load its SKILL.md, and record the choice in `context`.

    Mutates `context["skill_loaded"]` so HarnessLoop can echo it into telemetry
    without re-running detection. Returns "" when no framework matches.
    """
    from companybrain.harness.skills import detect_framework, load_skill

    framework = detect_framework(repo)
    if not framework:
        context["skill_loaded"] = None
        return ""

    skill_md = load_skill(framework)
    context["skill_loaded"] = framework
    if not skill_md:
        # We knew the framework but the SKILL.md is missing on disk. Still
        # record the detection so telemetry reflects reality.
        return ""

    return f"\n\n# Framework Skill: {framework}\n\n{skill_md.rstrip()}\n"


def _build_memory_section(repo: Path, context: dict[str, Any]) -> str:
    """Append the repo's BRAIN.md verbatim under a clear heading.

    Mutates `context["brain_md_loaded"]` to a bool so callers can tell whether
    the agent actually saw repo memory.
    """
    from companybrain.harness import memory

    brain_md = memory.load(repo)
    context["brain_md_loaded"] = bool(brain_md)
    if not brain_md:
        return ""
    return f"\n\n# Repo memory (BRAIN.md)\n\n{brain_md.rstrip()}\n"
