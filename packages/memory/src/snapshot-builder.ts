/**
 * packages/memory/src/snapshot-builder.ts
 *
 * SnapshotBuilder — assembles a ScopeSnapshot from live Neo4j graph data.
 *
 * The snapshot is a tiered compression of everything an AI agent needs to
 * understand a codebase scope without re-querying the graph on every turn:
 *
 *   Hot  tier — top 30 active files, last 7 days of commits, drift signals,
 *               project context, API contract summaries.
 *   Warm tier — top 50 symbols by caller count, data assumptions, known issues.
 *   Cold tier — archived URNs (no content, just references).
 *
 * Token budget: exportToYaml() fits the hot tier in ≤2000 tokens by truncating
 * long arrays and adding "…N more" hints.
 */

import yaml from "js-yaml";
import type { GraphClient } from "@company-brain/graph";
import { ScopeSnapshotSchema } from "./types.js";
import {
  computeStalenessScore,
} from "./staleness.js";
import type {
  ScopeSnapshot,
  HotTier,
  WarmTier,
  ColdTier,
  FileMemory,
  SymbolMemory,
  RecentCommit,
  DriftSignalSummary,
  ApiContractSummary,
} from "./types.js";

// ── Hot-tier token budget ─────────────────────────────────────────────────────

const HOT_MAX_ACTIVE_FILES  = 30;
const HOT_MAX_COMMITS       = 20;   // 7 days worth, capped
const HOT_MAX_DRIFT_SIGNALS = 10;
const HOT_MAX_API_CONTRACTS = 15;
const WARM_MAX_SYMBOLS      = 50;

/** Approximate token limit for the YAML hot-tier export */
const HOT_TIER_TOKEN_BUDGET = 2000;
/** Rough chars-per-token estimate for YAML */
const CHARS_PER_TOKEN       = 4;
const HOT_TIER_CHAR_BUDGET  = HOT_TIER_TOKEN_BUDGET * CHARS_PER_TOKEN;

// ── Builder ───────────────────────────────────────────────────────────────────

export class SnapshotBuilder {
  constructor(private readonly graph: GraphClient) {}

  // ── Public API ──────────────────────────────────────────────────────────────

  /**
   * Query the graph and build a complete ScopeSnapshot.
   *
   * @param scope     - Org/repo scope string, e.g. "acme/web"
   * @param commitSha - HEAD commit SHA at build time
   */
  async buildSnapshot(scope: string, commitSha: string): Promise<ScopeSnapshot> {
    const [hotTier, warmTier, coldTier] = await Promise.all([
      this._buildHotTier(scope, commitSha),
      this._buildWarmTier(scope, commitSha),
      this._buildColdTier(scope),
    ]);

    return {
      version:           "1",
      scope,
      generatedAtCommit: commitSha,
      generatedAt:       new Date().toISOString(),
      hotTier,
      warmTier,
      coldTier,
      staleSince:        null,
    };
  }

  /**
   * Serialize a ScopeSnapshot to YAML, fitting the hot tier in ~2000 tokens.
   * Long arrays are truncated with "…N more" hints.
   */
  async exportToYaml(snapshot: ScopeSnapshot): Promise<string> {
    // Build a budget-aware representation
    const hotCompressed = this._compressHotTierForExport(snapshot.hotTier);

    const exportable = {
      version:           snapshot.version,
      scope:             snapshot.scope,
      generatedAtCommit: snapshot.generatedAtCommit,
      generatedAt:       snapshot.generatedAt,
      staleSince:        snapshot.staleSince ?? undefined,
      hot:               hotCompressed,
      warm: {
        frequentSymbols: snapshot.warmTier.frequentSymbols.map(s => ({
          urn:            s.urn,
          name:           s.name,
          kind:           s.kind,
          signature:      s.signature,
          callerCount:    s.callers.length,
          calleeCount:    s.callees.length,
          contextSummary: s.contextSummary,
          confidence:     s.confidence,
          staleness:      s.stalenessScore,
        })),
        dataAssumptions: snapshot.warmTier.dataAssumptions,
        knownIssues:     snapshot.warmTier.knownIssues,
      },
      cold: {
        archivedContextUrnCount: snapshot.coldTier.archivedContextUrns.length,
        archivedContextUrns:     snapshot.coldTier.archivedContextUrns,
      },
    };

    return yaml.dump(exportable, {
      indent:        2,
      lineWidth:     120,
      noRefs:        true,
      sortKeys:      false,
    });
  }

  /**
   * Deserialize a YAML string back to a validated ScopeSnapshot.
   * Throws a ZodError if the document does not match the schema.
   */
  async importFromYaml(yamlStr: string): Promise<ScopeSnapshot> {
    const raw = yaml.load(yamlStr) as Record<string, unknown>;

    // Re-map export shape back to canonical shape
    const hotRaw  = raw.hot  as Record<string, unknown> | undefined;
    const warmRaw = raw.warm as Record<string, unknown> | undefined;
    const coldRaw = raw.cold as Record<string, unknown> | undefined;

    const canonical = {
      version:           raw.version,
      scope:             raw.scope,
      generatedAtCommit: raw.generatedAtCommit,
      generatedAt:       raw.generatedAt,
      staleSince:        raw.staleSince ?? null,
      hotTier: {
        activeFiles:      hotRaw?.activeFiles      ?? [],
        recentCommits:    hotRaw?.recentCommits     ?? [],
        openDriftSignals: hotRaw?.openDriftSignals  ?? [],
        projectContext:   hotRaw?.projectContext    ?? "",
        apiContracts:     hotRaw?.apiContracts      ?? [],
      },
      warmTier: {
        frequentSymbols: (warmRaw?.frequentSymbols as unknown[] ?? []).map(
          (s: unknown) => _expandSymbol(s as Record<string, unknown>),
        ),
        dataAssumptions: warmRaw?.dataAssumptions ?? [],
        knownIssues:     warmRaw?.knownIssues     ?? [],
      },
      coldTier: {
        archivedContextUrns: coldRaw?.archivedContextUrns ?? [],
      },
    };

    return ScopeSnapshotSchema.parse(canonical);
  }

  // ── Hot tier builder ────────────────────────────────────────────────────────

  private async _buildHotTier(scope: string, commitSha: string): Promise<HotTier> {
    const [activeFiles, recentCommits, openDriftSignals, apiContracts] =
      await Promise.all([
        this._queryActiveFiles(scope, commitSha),
        this._queryRecentCommits(scope),
        this._queryOpenDriftSignals(scope),
        this._queryApiContracts(scope),
      ]);

    return {
      activeFiles:      activeFiles.slice(0, HOT_MAX_ACTIVE_FILES),
      recentCommits:    recentCommits.slice(0, HOT_MAX_COMMITS),
      openDriftSignals: openDriftSignals.slice(0, HOT_MAX_DRIFT_SIGNALS),
      projectContext:   await this._buildProjectContext(scope),
      apiContracts:     apiContracts.slice(0, HOT_MAX_API_CONTRACTS),
    };
  }

  private async _queryActiveFiles(scope: string, commitSha: string): Promise<FileMemory[]> {
    // Files ranked by total edge count (most connected = most relevant)
    const rows = await this.graph.query<{
      id:                 string;
      attr_purpose:       string | null;
      attr_exports:       string | null;
      attr_key_symbols:   string | null;
      valid_from_commit:  string | null;
      attr_confidence:    number | null;
    }>(
      `MATCH (f:CBNode { type: "File", scope: $scope })
       WHERE f.valid_to_commit IS NULL
       OPTIONAL MATCH (f)-[r]-()
       WITH f, count(r) AS edgeCount
       ORDER BY edgeCount DESC
       LIMIT $limit
       RETURN properties(f) AS props`,
      { scope, limit: HOT_MAX_ACTIVE_FILES * 2 },
    );

    return rows.map(row => {
      const p = (row as unknown as { props: Record<string, unknown> }).props;
      const lastCommit = (p["valid_from_commit"] as string | null) ?? commitSha;
      const confidence = (p["attr_confidence"] as number | null) ?? 0.8;

      return {
        filePath:           _artifactFromUrn(p["id"] as string),
        purpose:            (p["attr_purpose"] as string | null) ?? "",
        exports:            _splitList(p["attr_exports"] as string | null),
        keySymbols:         _splitList(p["attr_key_symbols"] as string | null),
        lastModifiedCommit: lastCommit,
        stalenessScore:     computeStalenessScore(lastCommit, commitSha, confidence),
      };
    });
  }

  private async _queryRecentCommits(scope: string): Promise<RecentCommit[]> {
    const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();

    const rows = await this.graph.query<Record<string, unknown>>(
      `MATCH (c:CBNode { type: "Commit", scope: $scope })
       WHERE c.attr_timestamp >= $since
       RETURN properties(c) AS props
       ORDER BY c.attr_timestamp DESC
       LIMIT $limit`,
      { scope, since: sevenDaysAgo, limit: HOT_MAX_COMMITS },
    );

    return rows.map(row => {
      const p = (row as { props: Record<string, unknown> }).props;
      return {
        sha:       _artifactFromUrn(p["id"] as string),
        message:   (p["attr_message"]   as string | null) ?? "",
        author:    (p["attr_author"]    as string | null) ?? "",
        timestamp: (p["attr_timestamp"] as string | null) ?? "",
      };
    });
  }

  private async _queryOpenDriftSignals(scope: string): Promise<DriftSignalSummary[]> {
    const rows = await this.graph.query<Record<string, unknown>>(
      `MATCH (d:CBNode { type: "DriftSignal", scope: $scope })
       WHERE d.valid_to_commit IS NULL
         AND (d.attr_status IS NULL OR d.attr_status <> "resolved")
       RETURN properties(d) AS props
       ORDER BY d.attr_severity ASC
       LIMIT $limit`,
      { scope, limit: HOT_MAX_DRIFT_SIGNALS },
    );

    return rows.map(row => {
      const p = (row as { props: Record<string, unknown> }).props;
      const rawSeverity = (p["attr_severity"] as string | null) ?? "info";
      const severity: DriftSignalSummary["severity"] =
        rawSeverity === "breaking" ? "breaking"
        : rawSeverity === "warning" ? "warning"
        : "info";

      return {
        urn:            (p["id"]                  as string) ?? "",
        severity,
        description:    (p["attr_description"]    as string | null) ?? "",
        detectedFields: _splitList(p["attr_detected_fields"] as string | null),
      };
    });
  }

  private async _queryApiContracts(scope: string): Promise<ApiContractSummary[]> {
    const rows = await this.graph.query<Record<string, unknown>>(
      `MATCH (c:CBNode { type: "HTTPEndpoint", scope: $scope })
       WHERE c.valid_to_commit IS NULL
       RETURN properties(c) AS props
       LIMIT $limit`,
      { scope, limit: HOT_MAX_API_CONTRACTS * 2 },
    );

    return rows.map(row => {
      const p = (row as { props: Record<string, unknown> }).props;
      return {
        operationId: (p["attr_operation_id"] as string | null) ?? "",
        method:      (p["attr_method"]       as string | null) ?? "GET",
        path:        (p["attr_path"]         as string | null) ?? "",
        summary:     (p["attr_summary"]      as string | null) ?? "",
        deprecated:  Boolean(p["attr_deprecated"]),
      };
    });
  }

  private async _buildProjectContext(scope: string): Promise<string> {
    // Pull any NarrativeNote tagged as "project_context"
    const rows = await this.graph.query<Record<string, unknown>>(
      `MATCH (n:CBNode { type: "NarrativeNote", scope: $scope })
       WHERE n.attr_tag = "project_context" AND n.valid_to_commit IS NULL
       RETURN properties(n) AS props
       ORDER BY n.extracted_at DESC
       LIMIT 1`,
      { scope },
    );

    if (rows.length === 0) return `Scope: ${scope}`;
    const p = (rows[0] as { props: Record<string, unknown> }).props;
    return (p["attr_content"] as string | null) ?? `Scope: ${scope}`;
  }

  // ── Warm tier builder ───────────────────────────────────────────────────────

  private async _buildWarmTier(scope: string, commitSha: string): Promise<WarmTier> {
    const [frequentSymbols, dataAssumptions, knownIssues] = await Promise.all([
      this._queryFrequentSymbols(scope, commitSha),
      this._queryDataAssumptions(scope),
      this._queryKnownIssues(scope),
    ]);

    return { frequentSymbols, dataAssumptions, knownIssues };
  }

  private async _queryFrequentSymbols(scope: string, commitSha: string): Promise<SymbolMemory[]> {
    // Symbols ranked by number of inbound CALLS_TO edges (caller count)
    const rows = await this.graph.query<Record<string, unknown>>(
      `MATCH (sym:CBNode { type: "Function", scope: $scope })
       WHERE sym.valid_to_commit IS NULL
       OPTIONAL MATCH (caller:CBNode)-[:CALLS_TO]->(sym)
       OPTIONAL MATCH (sym)-[:CALLS_TO]->(callee:CBNode)
       WITH sym,
            collect(DISTINCT caller.id) AS callerUrns,
            collect(DISTINCT callee.id) AS calleeUrns,
            count(DISTINCT caller)      AS callerCount
       ORDER BY callerCount DESC
       LIMIT $limit
       RETURN properties(sym) AS props, callerUrns, calleeUrns`,
      { scope, limit: WARM_MAX_SYMBOLS },
    );

    return rows.map(row => {
      const r   = row as { props: Record<string, unknown>; callerUrns: string[]; calleeUrns: string[] };
      const p   = r.props;
      const lastCommit = (p["valid_from_commit"] as string | null) ?? commitSha;
      const confidence = (p["attr_confidence"]  as number | null) ?? 0.8;

      return {
        urn:            (p["id"]                   as string) ?? "",
        name:           (p["attr_name"]            as string | null) ?? "",
        kind:           ((p["attr_kind"]           as string | null) ?? "function") as SymbolMemory["kind"],
        signature:      (p["attr_signature"]       as string | null) ?? "",
        callers:        r.callerUrns ?? [],
        callees:        r.calleeUrns ?? [],
        contextSummary: (p["attr_context_summary"] as string | null) ?? "",
        confidence,
        stalenessScore: computeStalenessScore(lastCommit, commitSha, confidence),
      };
    });
  }

  private async _queryDataAssumptions(scope: string) {
    const rows = await this.graph.query<Record<string, unknown>>(
      `MATCH (n:CBNode { type: "DataAssumption", scope: $scope })
       WHERE n.valid_to_commit IS NULL
       RETURN properties(n) AS props
       LIMIT 30`,
      { scope },
    );

    return rows.map(row => {
      const p = (row as { props: Record<string, unknown> }).props;
      return {
        subject:    (p["attr_subject"]    as string | null) ?? "",
        assumption: (p["attr_assumption"] as string | null) ?? "",
        source:     (p["attr_source_uri"] as string | null) ?? "",
        confidence: (p["attr_confidence"] as number | null) ?? 0.5,
      };
    });
  }

  private async _queryKnownIssues(scope: string) {
    const rows = await this.graph.query<Record<string, unknown>>(
      `MATCH (n:CBNode { type: "KnownIssue", scope: $scope })
       WHERE n.valid_to_commit IS NULL
       RETURN properties(n) AS props
       LIMIT 20`,
      { scope },
    );

    return rows.map(row => {
      const p = (row as { props: Record<string, unknown> }).props;
      const rawSeverity = (p["attr_severity"] as string | null) ?? "medium";
      const severity =
        rawSeverity === "critical" ? "critical" as const
        : rawSeverity === "high"   ? "high"    as const
        : rawSeverity === "low"    ? "low"     as const
        : "medium" as const;

      return {
        id:           (p["id"]                  as string) ?? "",
        title:        (p["attr_title"]          as string | null) ?? "",
        severity,
        affectedUrns: _splitList(p["attr_affected_urns"] as string | null),
        notes:        (p["attr_notes"]          as string | null) ?? "",
      };
    });
  }

  // ── Cold tier builder ───────────────────────────────────────────────────────

  private async _buildColdTier(scope: string): Promise<ColdTier> {
    const rows = await this.graph.query<{ urn: string }>(
      `MATCH (n:CBNode { scope: $scope })
       WHERE n.valid_to_commit IS NOT NULL
         AND n.status = "removed"
       RETURN n.id AS urn
       LIMIT 500`,
      { scope },
    );

    return {
      archivedContextUrns: rows.map(r => r.urn),
    };
  }

  // ── YAML hot-tier compression ───────────────────────────────────────────────

  private _compressHotTierForExport(hot: HotTier): Record<string, unknown> {
    let budget = HOT_TIER_CHAR_BUDGET;

    // projectContext first (always included, but capped at 500 chars)
    const ctx = hot.projectContext.slice(0, 500);
    budget -= ctx.length;

    // Active files — strip large arrays, add counts
    const fileEntries: unknown[] = [];
    for (const f of hot.activeFiles) {
      if (budget <= 0) {
        fileEntries.push(`...${hot.activeFiles.length - fileEntries.length} more`);
        break;
      }
      const entry = {
        path:      f.filePath,
        purpose:   f.purpose,
        exports:   f.exports.slice(0, 5),
        ...(f.exports.length > 5 ? { exportsMore: f.exports.length - 5 } : {}),
        staleness: _round(f.stalenessScore),
      };
      const approxSize = JSON.stringify(entry).length;
      budget -= approxSize;
      fileEntries.push(entry);
    }

    // Recent commits — summary only
    const commitEntries = hot.recentCommits.slice(0, 10).map(c => ({
      sha:    c.sha.slice(0, 8),
      msg:    c.message.slice(0, 72),
      author: c.author,
      ts:     c.timestamp.slice(0, 10),
    }));
    const commitMore = hot.recentCommits.length - commitEntries.length;

    // Drift signals
    const driftEntries = hot.openDriftSignals.slice(0, 5).map(d => ({
      urn:         d.urn,
      severity:    d.severity,
      description: d.description.slice(0, 120),
    }));
    const driftMore = hot.openDriftSignals.length - driftEntries.length;

    // API contracts
    const contractEntries = hot.apiContracts.slice(0, 10).map(c => ({
      op:         c.operationId,
      method:     c.method,
      path:       c.path,
      summary:    c.summary.slice(0, 80),
      deprecated: c.deprecated || undefined,
    }));
    const contractMore = hot.apiContracts.length - contractEntries.length;

    return {
      projectContext: ctx,
      activeFiles:    fileEntries,
      recentCommits: [
        ...commitEntries,
        ...(commitMore > 0 ? [`...${commitMore} more`] : []),
      ],
      openDriftSignals: [
        ...driftEntries,
        ...(driftMore > 0 ? [`...${driftMore} more`] : []),
      ],
      apiContracts: [
        ...contractEntries,
        ...(contractMore > 0 ? [`...${contractMore} more`] : []),
      ],
    };
  }
}

// ── Private helpers ───────────────────────────────────────────────────────────

/** Extract the artifact segment from a URN string */
function _artifactFromUrn(urn: string): string {
  if (!urn) return "";
  // urn:cb:<source>:<scope>:<artifact>[:<symbol>]
  const parts = urn.split(":");
  return parts.slice(4).join(":") || urn;
}

/** Split a pipe- or comma-separated list stored as a Neo4j string property */
function _splitList(raw: string | null): string[] {
  if (!raw) return [];
  return raw.split(/[|,]/).map(s => s.trim()).filter(Boolean);
}

/** Round a float to 2 decimal places for cleaner YAML */
function _round(n: number): number {
  return Math.round(n * 100) / 100;
}

/** Re-expand a compacted symbol entry from YAML import back to SymbolMemory shape */
function _expandSymbol(s: Record<string, unknown>): Record<string, unknown> {
  return {
    urn:            s["urn"]            ?? "",
    name:           s["name"]          ?? "",
    kind:           s["kind"]          ?? "function",
    signature:      s["signature"]     ?? "",
    callers:        s["callers"]       ?? [],
    callees:        s["callees"]       ?? [],
    contextSummary: s["contextSummary"] ?? "",
    confidence:     s["confidence"]    ?? 0.5,
    stalenessScore: s["staleness"]     ?? s["stalenessScore"] ?? 0,
  };
}
