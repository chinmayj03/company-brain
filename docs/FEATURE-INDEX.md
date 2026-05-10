# Harness feature index — ADR-0051 P1–P4

One-line pointer per harness feature → its source module + driving
sub-section of the ADR. Use this to navigate "where does X live in code?"
without grepping. ADR-0051 §"Phase N" tables enumerate the design intent;
this file just lists the landed implementation.

## P1 — Tool-use loop

| #  | Feature                              | Source                                                      |
|----|--------------------------------------|-------------------------------------------------------------|
| 1  | HarnessLoop (assistant ↔ tool)       | [harness/loop.py](../company-brain-ai/src/companybrain/harness/loop.py) |
| 2  | Tool registry + decorator            | [harness/tools/__init__.py](../company-brain-ai/src/companybrain/harness/tools/__init__.py) |
| 3  | System prompt builder                | [harness/system_prompt.py](../company-brain-ai/src/companybrain/harness/system_prompt.py) |
| 4  | P1 tool wrappers (read/extract/store)| [harness/tools/](../company-brain-ai/src/companybrain/harness/tools/) |

## P2 — Sub-agents and parallel fan-out

| #  | Feature                              | Source                                                      |
|----|--------------------------------------|-------------------------------------------------------------|
| 5  | Subagent isolated runner             | [harness/subagent.py](../company-brain-ai/src/companybrain/harness/subagent.py) |
| 6  | spawn_extractor / spawn_research / spawn_verifier | [harness/tools/spawn_*.py](../company-brain-ai/src/companybrain/harness/tools/) |
| 7  | Per-sub-agent timeout + concurrency  | [harness/subagent.run_with_timeout](../company-brain-ai/src/companybrain/harness/subagent.py) |
| 8  | Sub-agent cost roll-up               | [harness/subagent.SubagentResult.cost_usd](../company-brain-ai/src/companybrain/harness/subagent.py) |

## P3 — Skills + per-repo memory

| #  | Feature                              | Source                                                      |
|----|--------------------------------------|-------------------------------------------------------------|
| 9  | Framework detection                  | [harness/skills.py](../company-brain-ai/src/companybrain/harness/skills.py) |
| 10 | Per-framework SKILL.md injection     | [harness/system_prompt._build_skill_section](../company-brain-ai/src/companybrain/harness/system_prompt.py) |
| 11 | BRAIN.md auto-load                   | [harness/memory.py](../company-brain-ai/src/companybrain/harness/memory.py) |
| 12 | BRAIN.md auto-append + dedupe        | [harness/memory.auto_append](../company-brain-ai/src/companybrain/harness/memory.py) |

## P4 — Hooks + permissions + streaming + introspection

| #   | Feature                              | Source                                                      |
|-----|--------------------------------------|-------------------------------------------------------------|
| 17  | Hooks at 9 events                    | [harness/hooks.py](../company-brain-ai/src/companybrain/harness/hooks.py) |
| 21  | Hook example scripts (executable templates) | [.brain-template/hooks/](../company-brain-ai/.brain-template/hooks/) |
| 22  | Per-tool capability declarations     | [harness/permissions.py](../company-brain-ai/src/companybrain/harness/permissions.py) + each `tools/*.py` |
| 25  | Permission gate in dispatch          | [harness/loop.HarnessLoop._dispatch](../company-brain-ai/src/companybrain/harness/loop.py) |
| 31  | TodoList streaming over SSE          | [harness/progress.py](../company-brain-ai/src/companybrain/harness/progress.py) + [api/routes/stream.py](../company-brain-ai/src/companybrain/api/routes/stream.py) |
| 34  | Auto-compaction at 80% context fill  | [harness/compaction.py](../company-brain-ai/src/companybrain/harness/compaction.py) |
| A5  | Per-tool-call cost telemetry         | [harness/cost.py](../company-brain-ai/src/companybrain/harness/cost.py) |
| A6  | Session model (list / resume / transcript) | [harness/session.py](../company-brain-ai/src/companybrain/harness/session.py) |
| A7  | CLI: `brain session …`               | [cli.session_cmd](../company-brain-ai/src/companybrain/cli.py) |
| A9  | CLI: `brain tools list`              | [cli.tools_cmd](../company-brain-ai/src/companybrain/cli.py) |
| A11 | Hook lifecycle wiring in HarnessLoop | [harness/loop.HarnessLoop._fire_hook](../company-brain-ai/src/companybrain/harness/loop.py) |
| A15 | Tunables in Settings                 | [config.Settings](../company-brain-ai/src/companybrain/config.py) — `hooks_enabled`, `hook_timeout_s`, `compaction_threshold`, `compaction_context_limit_tokens`, `grants_auto_approve` |

## Configuration knobs (env / settings)

| Knob                                 | Default        | Effect                                  |
|--------------------------------------|----------------|-----------------------------------------|
| `BRAIN_USE_HARNESS`                  | `false`        | Run pipeline through HarnessLoop        |
| `BRAIN_HOOKS_ENABLED`                | `true`         | Master switch for hook firing           |
| `BRAIN_AUTOAPPROVE`                  | `false`        | ASK → AUTO (CI / non-interactive)       |
| `BRAIN_GRANTS=cap:decision,...`      | (empty)        | Override DEFAULT_GRANTS at process start |
| `BRAIN_HOME`                         | `~/.brain`     | Session snapshot root                   |
| `MAX_SUBAGENTS`                      | `8`            | Sub-agent fan-out width per spawn_*     |
| `SUBAGENT_TIMEOUT_S`                 | `120`          | Per-sub-agent wall-clock cap            |
