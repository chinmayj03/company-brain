# ADR-0003: Extractor Plugin Contract

**Status:** Accepted  
**Date:** 2026-05-03  
**Deciders:** Chinmay  
**Depends on:** ADR-0001 (URN scheme), ADR-0002 (graph storage)  
**Unblocks:** Phase 1 (core-ts extractor), Phase 3 (framework extractors)

---

## Context

The Company Brain knowledge graph is populated by **extractors** — programs that read source artifacts (files, git history, API specs, ticket systems) and emit typed nodes and edges conforming to the schema.

Multiple extractors will coexist:
- `extractors/git` — Repository, Commit, Branch, PullRequest from git history
- `extractors/core-ts` — TypeScript AST via tree-sitter + ts-morph
- `extractors/framework-next` — Next.js routes, pages, layouts
- `extractors/framework-prisma` — Prisma schema → DB nodes
- `extractors/framework-openapi` — OpenAPI spec → Contract nodes
- `extractors/docs-md` — PRDs, ADRs from markdown files
- `extractors/bridge` — Code↔business bridge layer

These extractors:
- Must be **isolated**: a crash in the Prisma extractor must not break the git extractor
- Must be **incremental**: given a dirty-set of changed files, re-extract only the affected subgraph
- Must be **deterministic**: same input + same extractor version = same output
- Must be **idempotent**: running twice on the same commit produces no spurious DB writes
- Must carry **provenance**: every node/edge they emit includes `extractor` name+version

The plugin contract defines the interface that all extractors implement.

---

## Decision

Every extractor exports a **named class** implementing the `Extractor` interface. The interface is defined in `packages/schema` and is the single coupling point between extractors and the pipeline host.

### The `Extractor` interface

```ts
// packages/schema/src/extractor.ts

export interface ExtractorManifest {
  /** Machine-readable extractor identifier. Used in provenance fields. */
  name: string;                // e.g. "git", "core-ts", "framework-next"
  version: string;             // semver, e.g. "0.1.0"
  /** Human-readable description for logs and the admin UI. */
  description: string;
  /** Source artifact types this extractor handles. */
  handles: ArtifactKind[];     // e.g. ["git_repo", "typescript_file"]
  /** Node types this extractor produces. */
  emits: NodeType[];
}

export interface ExtractorContext {
  /** Scope string for URN construction: "<org>/<repo>". */
  scope: string;
  /** The commit SHA being extracted. Used for provenance. */
  commitSha: string;
  /** Absolute path to the local checkout. */
  repoRoot: string;
  /** Set of relative file paths that changed vs. prior extract.
   *  Empty = first-time full extraction. */
  dirtySet: string[];
  /** Write-through graph client (Neo4j). */
  graph: GraphClient;
  /** Logger (structured). */
  log: Logger;
}

export interface ExtractorResult {
  /** Nodes upserted into the graph. */
  nodesWritten: number;
  /** Edges upserted into the graph. */
  edgesWritten: number;
  /** Node URNs invalidated (set valid_to_commit). */
  nodesInvalidated: number;
  /** Non-fatal warnings encountered during extraction. */
  warnings: string[];
  /** Duration in milliseconds. */
  durationMs: number;
}

export interface Extractor {
  readonly manifest: ExtractorManifest;
  /**
   * Run the extractor.
   *
   * The extractor must:
   * 1. Compute URNs for all nodes/edges it will write.
   * 2. Upsert nodes/edges into ctx.graph (never delete directly — use invalidate()).
   * 3. Invalidate nodes that no longer exist in the dirty files
   *    by setting valid_to_commit = ctx.commitSha.
   * 4. Return an ExtractorResult summarising what was written.
   *
   * Failures must throw — the pipeline host catches and continues with
   * other extractors.  Never swallow errors silently.
   */
  extract(ctx: ExtractorContext): Promise<ExtractorResult>;
}
```

### Isolation model

The pipeline host (`apps/extractor-worker`) runs each extractor in isolation:

```
ExtractorWorker
  for each extractor in registry:
    try:
      result = await extractor.extract(ctx)
      log(result)
    catch (err):
      log.error(extractor.manifest.name, err)
      continue   ← next extractor runs regardless
```

A crashed extractor does not abort the pipeline. Its failure is logged with the extractor name and version for triage.

### Incrementality contract

The pipeline host computes the `dirtySet` (files changed since last extract via git diff) and passes it to every extractor. Each extractor decides which of its work is affected:

- **File-scoped extractors** (core-ts, framework-next): re-extract only files in `dirtySet`
- **Whole-repo extractors** (git): always run; they are fast and idempotent
- **Cross-file extractors** (bridge, summaries): must also re-run for files whose *imports* are in `dirtySet`

The extractor is responsible for computing its own affected set from `dirtySet`. This is intentional — only the extractor knows its own dependency graph.

### Provenance requirement

Every node and edge written **must** include:

```ts
extractor: { name: manifest.name, version: manifest.version }
extracted_from_commit: ctx.commitSha
extraction_timestamp: new Date().toISOString()
confidence: 0.0..1.0   // required, not optional
derivation: 'ast' | 'lsp' | 'config' | 'llm' | 'human' | 'api'
```

The `packages/graph` `GraphClient.upsertNode()` method enforces this at runtime and throws if provenance fields are missing.

### Registering an extractor

Add it to `apps/extractor-worker/src/registry.ts`:

```ts
export const EXTRACTORS: Extractor[] = [
  new GitExtractor(),
  new CoreTsExtractor(),
  new FrameworkNextExtractor(),
  // ...
];
```

No dynamic loading, no plugin discovery at startup. The registry is a plain TypeScript array so the build tool can tree-shake unused extractors.

---

## Consequences

- Every extractor is a class with a `manifest` and an `extract()` method. No other exports are required.
- The `packages/schema` package is the only dependency required by all extractors (for URN building and type imports).
- Extractors may depend on `packages/graph` for Neo4j writes and on each other's types, but not on each other's runtime logic.
- A broken extractor is never silently skipped — it logs an error with its name/version so the operator can investigate.
- Extractor versioning: when an extractor improves (emits richer nodes), its semver `patch` bumps. When its schema changes (emits new node types or removes old ones), its `minor` bumps. Old node/edge data from prior extractor versions is preserved until the extractor explicitly invalidates it on next run.

### Testing requirement

Every extractor must have at minimum:
- A **determinism test**: run twice on the same fixture → identical node/edge set
- An **idempotency test**: upsert the same node twice → one row in Neo4j
- A **provenance test**: every written node has all required provenance fields

See `eval/fixtures/` for the reference fixture repos used in extractor tests.
