/**
 * packages/schema/src/urn.ts
 *
 * URN identity scheme for Company Brain nodes and edges.
 * See ADR-0001 for the full specification.
 *
 * Format: urn:cb:<source>:<scope>:<artifact>[:<symbol>][@<version>]
 *
 * All extractors MUST use buildUrn() — never construct URN strings manually.
 */

/** Allowed source systems for URN construction. */
export type UrnSource =
  | "repo"      // git repository artifacts
  | "file"      // source files
  | "symbol"    // code symbols (functions, classes, constants)
  | "contract"  // OpenAPI / GraphQL / Protobuf contracts
  | "schema"    // database schemas (Prisma, SQL)
  | "next"      // Next.js framework nodes (routes, screens, layouts, components)
  | "drift"     // Drift signals (cross-extractor)
  | "linear"    // Linear tickets
  | "notion"    // Notion pages
  | "jira"      // Jira tickets
  | "slack"     // Slack threads
  | "prd"       // Product Requirements Documents
  | "adr"       // Architecture Decision Records
  | "narrative" // NarrativeNote (LLM/human authored)
  | "figma";    // Figma frames

export interface UrnParts {
  source: UrnSource;
  /** Org/repo scope: e.g. "acme/web" */
  scope: string;
  /** Artifact identifier: file path, ticket ID, schema name, etc. */
  artifact: string;
  /** Symbol within the artifact (for code nodes). */
  symbol?: string;
  /** Semantic or schema version (for contracts). */
  version?: string;
}

const URN_PREFIX = "urn:cb:" as const;
const ALLOWED_CHARS = /^[A-Za-z0-9/_.:@\-]+$/;

/**
 * Build a stable URN from structured parts.
 *
 * @example
 * buildUrn({ source: "symbol", scope: "acme/web",
 *            artifact: "src/billing/handler.ts",
 *            symbol: "BillingService.charge" })
 * // → "urn:cb:symbol:acme/web:src/billing/handler.ts:BillingService.charge"
 */
export function buildUrn(parts: UrnParts): string {
  const { source, scope, artifact, symbol, version } = parts;

  if (!scope) throw new Error("URN scope must be non-empty");
  if (!artifact) throw new Error("URN artifact must be non-empty");

  let urn = `${URN_PREFIX}${source}:${scope}:${artifact}`;
  if (symbol) urn += `:${symbol}`;
  if (version) urn += `@${version}`;

  assertValidUrn(urn);
  return urn;
}

/**
 * Parse a URN string back into its constituent parts.
 * Returns null for invalid URNs (does not throw).
 */
export function parseUrn(urn: string): UrnParts | null {
  if (!urn.startsWith(URN_PREFIX)) return null;

  const rest = urn.slice(URN_PREFIX.length);
  const [source, scope, ...artifactParts] = rest.split(":");

  if (!source || !scope || artifactParts.length === 0) return null;

  // The last segment may contain @version
  const last = artifactParts[artifactParts.length - 1] ?? "";
  let version: string | undefined;
  let cleanLast = last;
  const atIdx = last.lastIndexOf("@");
  if (atIdx > 0) {
    version = last.slice(atIdx + 1);
    cleanLast = last.slice(0, atIdx);
    artifactParts[artifactParts.length - 1] = cleanLast;
  }

  // For symbol URNs: artifact is [0], symbol is [1]+ (method names can contain dots)
  // We reconstruct by joining with ":"
  const fullPath = artifactParts.join(":");

  // Try to split off a symbol: everything after the LAST colon that follows
  // a file extension is the symbol. Heuristic: find the last segment after
  // a segment containing a "." (file extension marker).
  let artifact = fullPath;
  let symbol: string | undefined;

  const segments = fullPath.split(":");
  if (segments.length > 1) {
    // Find the first segment that looks like a file path (contains "/" or ".")
    const fileSegIdx = segments.findIndex(s => s.includes("/") || s.includes("."));
    if (fileSegIdx >= 0 && fileSegIdx < segments.length - 1) {
      artifact = segments.slice(0, fileSegIdx + 1).join(":");
      symbol = segments.slice(fileSegIdx + 1).join(":");
    }
  }

  return {
    source: source as UrnSource,
    scope,
    artifact,
    ...(symbol ? { symbol } : {}),
    ...(version !== undefined ? { version } : {}),
  };
}

/**
 * Assert that a string is a valid Company Brain URN.
 * Throws an Error if invalid.
 */
export function assertValidUrn(urn: string): asserts urn is string {
  if (!urn.startsWith(URN_PREFIX)) {
    throw new Error(`URN must start with "${URN_PREFIX}": ${urn}`);
  }
  // Strip the @version suffix before char-validation
  const withoutVersion = urn.replace(/@[^:@]+$/, "");
  if (!ALLOWED_CHARS.test(withoutVersion.slice(URN_PREFIX.length))) {
    throw new Error(
      `URN contains invalid characters (allowed: A-Za-z0-9/_.:@-): ${urn}`
    );
  }
  if (urn.length > 512) {
    throw new Error(`URN exceeds 512 character limit: ${urn.length} chars`);
  }
}

/** Convenience — true if the string is a valid URN. */
export function isValidUrn(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    assertValidUrn(value);
    return true;
  } catch {
    return false;
  }
}

// ── Convenience builders for common node types ────────────────────────────────

export const Urn = {
  repository: (scope: string) =>
    buildUrn({ source: "repo", scope, artifact: "" }).replace(/:$/, ""),

  branch: (scope: string, branchName: string) =>
    buildUrn({ source: "repo", scope, artifact: branchName }),

  commit: (scope: string, sha: string) =>
    buildUrn({ source: "repo", scope, artifact: sha }),

  file: (scope: string, filePath: string) =>
    buildUrn({ source: "file", scope, artifact: filePath }),

  symbol: (scope: string, filePath: string, qualifiedName: string) =>
    buildUrn({ source: "symbol", scope, artifact: filePath, symbol: qualifiedName }),

  pullRequest: (scope: string, prNumber: number) =>
    buildUrn({ source: "repo", scope, artifact: `pr/${prNumber}` }),

  linearTicket: (orgSlug: string, ticketId: string) =>
    buildUrn({ source: "linear", scope: orgSlug, artifact: ticketId }),

  notionPage: (orgSlug: string, pageId: string) =>
    buildUrn({ source: "notion", scope: orgSlug, artifact: `page/${pageId}` }),

  openApiOperation: (scope: string, operationId: string, version?: string) =>
    buildUrn({ source: "contract", scope, artifact: `operations/${operationId}`, ...(version ? { version } : {}) }),

  prismaModel: (scope: string, modelName: string) =>
    buildUrn({ source: "schema", scope, artifact: modelName }),

  prismaField: (scope: string, modelName: string, fieldName: string) =>
    buildUrn({ source: "schema", scope, artifact: modelName, symbol: fieldName }),
} as const;
