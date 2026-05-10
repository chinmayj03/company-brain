# Slash commands (ADR-0052 P5)

The harness recognises a small set of `/<name>` commands. Typing one as the
first token of a user message expands a markdown template (with positional
arguments interpolated) and prepends it to the harness loop, so the agent
sees the canonical instruction for that workflow rather than free-form text.

## Available commands

| Command   | Args                          | What it does |
|-----------|-------------------------------|--------------|
| `/extract`  | `<endpoint> [METHOD]`         | Run the canonical extraction pipeline for one endpoint. METHOD defaults to `GET`. |
| `/query`    | `<question>`                  | Natural-language query against the brain (cited where possible). |
| `/verify`   | `<urn>`                       | Spawn a verifier sub-agent that re-derives a claim from primary sources. |
| `/diff`     | `<commit_a> <commit_b>`       | Show entity-level diff between two repo states. |
| `/cost`     |                               | Print the most recent run's cost summary. |
| `/explain`  | `<method_qname>`              | Plain-prose explanation of one method, grounded in code. |
| `/wipe`     |                               | Clear the workspace's brain data (with explicit confirm). |
| `/stats`    |                               | Brain entity counts by type. |
| `/init`     |                               | Bootstrap a new repo's `.brain/BRAIN.md` and hooks dir. |
| `/skills`   | `[list \| show <framework>]`  | List available framework skills (or show one). |

## Authoring a new command

Drop a markdown file in
[`src/companybrain/harness/commands/`](../company-brain-ai/src/companybrain/harness/commands/).
The file MUST start with a YAML frontmatter block:

```markdown
---
name: my-command
description: One-line description shown in /help.
args:
  - name: target
    type: string
    required: true
  - name: format
    type: string
    required: false
    default: json
---
You are doing X for {target}. Output as {format}.
```

The body is a Jinja-style template; `{name}` placeholders are substituted from
the parsed arguments. The last declared `arg` absorbs any trailing tokens, so
free-form questions like `/explain SomeClass.someMethod with the new flag`
work as expected.

## How it ties into the loop

`HarnessLoop.run(user_message, …)` doesn't know about slash commands directly
— callers (the SDK, the API route, future IDE clients) call
`parse_and_render(user_message)` first and pass the rendered string back into
`loop.run`. The returned `(rendered, command_name)` lets telemetry mark which
command produced a run.

## Testing

`tests/unit/test_slash_commands.py` covers the parser, registry, and template
substitution. The acceptance suite asserts the bundled ten commands all route
correctly. Add a unit test for any new command that has non-trivial template
logic.
