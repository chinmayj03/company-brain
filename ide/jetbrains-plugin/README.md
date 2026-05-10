# Company Brain — JetBrains plugin (skeleton)

Scaffold for the JetBrains version of the Company Brain IDE integration. Ships
in ADR-0052 P7 alongside the VS Code extension but is **not** published to the
JetBrains Marketplace yet — that happens after the VS Code release proves out
the wire format and UX.

## Status

- Gradle build configured against IntelliJ Platform 2024.1.
- Single placeholder action (`AskBrainAction`) mounted on the editor popup
  menu so the plugin loads without errors.
- No MCP client yet. The real implementation will mirror the VS Code wire
  format (JSON-RPC over `POST /mcp`).

## Build (sandbox)

```bash
cd ide/jetbrains-plugin
./gradlew buildPlugin
./gradlew runIde   # opens a sandbox IntelliJ with the plugin installed
```

The sandbox build is intentionally not part of CI — it's slow and the plugin
provides no real functionality yet.
