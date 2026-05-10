# Plugin authoring guide (ADR-0052 P6)

A **plugin** is a zip bundle that extends the company-brain harness with
team-specific knowledge: framework skills, slash commands, hooks, and tool
definitions. Plugins live under `~/.brain/plugins/` and are managed by the
`brain plugin` CLI.

This guide walks through building one.

---

## 1. Bundle layout

```
my-plugin.zip
├── plugin.json        ← required manifest
├── skills/
│   └── <framework>/
│       └── SKILL.md   ← optional framework-skill override
├── hooks/             ← optional hook scripts (referenced from settings.json)
├── commands/          ← optional slash commands consumed by harness/commands/
└── tools/             ← optional tool definitions (advisory; not auto-loaded today)
```

Only `plugin.json` is mandatory. Extra files are ignored unless the relevant
loader knows about them.

---

## 2. The manifest (`plugin.json`)

```json
{
  "name":        "acme-spring-boot",
  "version":     "0.1.0",
  "description": "Acme house-style Spring Boot conventions.",
  "required_capabilities": ["read_code", "write_brain"],
  "homepage":    "https://example.com/plugins/acme-spring-boot"
}
```

| Field                   | Required | Purpose                                                                                                                                                       |
|-------------------------|----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `name`                  | yes      | Stable identifier. Determines the install dir (`~/.brain/plugins/<name>/`). Lowercase, hyphen-separated.                                                      |
| `version`               | no       | Semantic version. Defaults to `0.0.0`.                                                                                                                        |
| `description`           | no       | Free-form one-liner; appears in `brain plugin list`.                                                                                                          |
| `required_capabilities` | no       | Capabilities the plugin's tools / hooks need. Future versions will refuse to install unless the user has granted them.                                        |
| `homepage`              | no       | Pointer to docs / source.                                                                                                                                     |

---

## 3. Shipping a framework skill

The harness loads at most one `SKILL.md` per run, picked by
`harness.skills.detect_framework()`. When a plugin ships a `SKILL.md` for the
same framework name, **the plugin version wins** — that's the entire point.

Layout:

```
skills/spring-boot/SKILL.md
```

The framework name is the parent directory; pick from the catalogue exposed
by `harness.skills.AVAILABLE_FRAMEWORKS`:

```
spring-boot · fastapi · nestjs · django · rails · nextjs
```

The `SKILL.md` body is injected verbatim under a `# Framework Skill: <name>`
heading, so don't lead with your own H1.

A reference example ships in `company-brain-ai/fixtures/plugins/acme-spring-boot/`.

---

## 4. Building the bundle

```bash
cd my-plugin/
zip -r ../my-plugin.zip plugin.json skills/ hooks/ commands/
```

The zip's top-level entries become the plugin root. Don't nest the bundle
inside another directory.

### Safety

The installer rejects archive members whose normalised path escapes the
plugin directory (CVE-2007-4559 zip-slip). If you have legitimate symlinks
in your tree, expand them before zipping.

---

## 5. Installing & inspecting

```bash
# Local file
brain plugin install ./my-plugin.zip

# URL (no auth — caller is responsible for trust)
brain plugin install https://example.com/my-plugin.zip

# What's installed?
brain plugin list

# Remove
brain plugin uninstall my-plugin
```

`PLUGIN_HOME` defaults to `~/.brain/plugins/`. Override with the
`BRAIN_PLUGIN_HOME` env var when running tests or pinning to a project-local
directory.

---

## 6. Verifying a skill override

Once installed, the plugin's `SKILL.md` is consulted before the bundled tree:

```python
from companybrain.harness import skills

text = skills.load_skill("spring-boot")
# Plugin override surfaces here.
```

The fast path also works in the live harness — running `brain index` against
a Spring Boot repo will pick up the plugin's conventions automatically.

---

## 7. Roadmap (future P-versions)

* **Hooks** — settings.local.json paths can already point inside a plugin's
  `hooks/` directory, but no plugin-driven auto-registration yet.
* **Slash commands** — the loader for `commands/` will land alongside the
  next batch of harness slash commands.
* **Tool definitions** — once a plugin's `required_capabilities` are honoured
  end-to-end, plugins will be able to register new tools that the harness
  loop dispatches the same way it dispatches built-ins.

Until those land, treat plugins as a `SKILL.md` override channel — that
alone is enough to override our default Spring Boot / FastAPI conventions
with house-style ones.
