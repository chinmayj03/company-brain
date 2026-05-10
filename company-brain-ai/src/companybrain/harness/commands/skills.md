---
name: skills
description: List available framework skills (or show one).
args:
  - name: subcommand
    type: string
    required: false
    default: list
---
{subcommand}

If the subcommand above reads "list" (the default), enumerate every framework
skill available in the harness — call the harness's skills loader and print
the framework name + the first heading of its SKILL.md.

If the subcommand is "show <framework>", read that framework's SKILL.md and
print it verbatim.

Otherwise, ask the user to clarify.
