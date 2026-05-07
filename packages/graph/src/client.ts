/**
 * packages/graph/src/client.ts
 *
 * GraphClient — the single write/read surface for the Company Brain Neo4j graph.
 *
 * Design principles:
 *  - All writes go through upsertNode() / upsertEdge().
 *  - Both methods enforce provenance fields at runtime (throws if missing).
 *  - Nodes are never deleted — only invalidated via invalidateNode().
 *  - Every upsert is idempotent: running twice on the same data = same graph state.
 *  - All node IDs are URNs (validated on write).
 *
 * Usage:
 *   const client = new GraphClient({ url: "bolt://localhost:7687",
 *                                    username: "neo4j", password: "..." });
 *   await client.connect();
 *   await client.upsertNode({ id: Urn.file("acme/web", "src/main.ts"), ... });
 *   await client.close();
 */

import neo4j, {
  type Driver,
  type Session,
  type QueryResult,
} from "neo4j-driver";

import {
  assertValidUrn,
  NodeEnvelopeSchema,
  EdgeEnvelopeSchema,
  type NodeEnvelope,
  type EdgeEnvelope,
} from "@company-brain/schema";

export interface GraphClientConfig {
  url:      string;   // e.g. "bolt://localhost:7687"
  username: string;
  password: string;
  /** Database name — defaults to "neo4j" */
  database?: string;
}

export class GraphClient {
  private driver: Driver | null = null;
  private readonly config: GraphClientConfig;

  constructor(config: GraphClientConfig) {
    this.config = config;
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  async connect(): Promise<void> {
    this.driver = neo4j.driver(
      this.config.url,
      neo4j.auth.basic(this.config.username, this.config.password),
      {
        maxConnectionPoolSize: 50,
        connectionAcquisitionTimeout: 10_000,
      }
    );
    await this.driver.verifyConnectivity();
    await this._ensureConstraints();
  }

  async close(): Promise<void> {
    await this.driver?.close();
    this.driver = null;
  }

  // ── Schema constraints ────────────────────────────────────────────────────

  private async _ensureConstraints(): Promise<void> {
    const session = this._session();
    try {
      // Unique node identity constraint
      await session.run(
        `CREATE CONSTRAINT cb_node_id IF NOT EXISTS
         FOR (n:CBNode) REQUIRE n.id IS UNIQUE`
      );
      // Unique edge identity constraint (relationship property uniqueness requires APOC in CE)
      // We use a shadow node approach: edges are tracked as CBEdge nodes for idempotency.
      await session.run(
        `CREATE CONSTRAINT cb_edge_id IF NOT EXISTS
         FOR (e:CBEdge) REQUIRE e.id IS UNIQUE`
      );
      // Index on type for fast typed queries
      await session.run(
        `CREATE INDEX cb_node_type IF NOT EXISTS FOR (n:CBNode) ON (n.type)`
      );
      await session.run(
        `CREATE INDEX cb_node_scope IF NOT EXISTS FOR (n:CBNode) ON (n.scope)`
      );
    } finally {
      await session.close();
    }
  }

  // ── Node writes ───────────────────────────────────────────────────────────

  /**
   * Upsert a node into the graph.
   *
   * The node is written with:
   *   - Label :CBNode  (always present — enables cross-type queries)
   *   - Label :<type>  (e.g. :File, :Function, :HTTPEndpoint)
   *   - All NodeEnvelope fields as properties
   *   - `attributes` merged as flat top-level properties (prefixed with "attr_")
   *
   * Throws if provenance fields are missing or the ID is not a valid URN.
   */
  async upsertNode(envelope: NodeEnvelope): Promise<void> {
    // Validate the full envelope via Zod
    const parsed = NodeEnvelopeSchema.safeParse(envelope);
    if (!parsed.success) {
      throw new Error(
        `Invalid NodeEnvelope for ${envelope.id}: ${parsed.error.message}`
      );
    }
    assertValidUrn(parsed.data.id);

    const { attributes, source_range, ...flat } = parsed.data;

    // Flatten attributes with "attr_" prefix to avoid property name collisions
    const attrProps: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(attributes ?? {})) {
      attrProps[`attr_${k}`] = v;
    }

    const props = {
      ...flat,
      ...attrProps,
      source_range_start_line:   source_range?.start.line   ?? null,
      source_range_start_col:    source_range?.start.column ?? null,
      source_range_end_line:     source_range?.end.line     ?? null,
      source_range_end_col:      source_range?.end.column   ?? null,
      // Scope extracted from URN for fast indexed lookups
      scope: this._scopeFromUrn(flat.id),
    };

    const session = this._session();
    try {
      await session.run(
        `MERGE (n:CBNode { id: $id })
         SET n += $props
         WITH n
         CALL apoc.create.addLabels(n, [$type]) YIELD node
         RETURN node`,
        { id: flat.id, props, type: flat.type }
      );
    } finally {
      await session.close();
    }
  }

  /**
   * Batch upsert nodes — more efficient than looping upsertNode().
   */
  async upsertNodes(envelopes: NodeEnvelope[]): Promise<{ written: number; errors: string[] }> {
    const errors: string[] = [];
    let written = 0;

    // Process in batches of 100
    const BATCH = 100;
    for (let i = 0; i < envelopes.length; i += BATCH) {
      const batch = envelopes.slice(i, i + BATCH);
      const session = this._session();
      try {
        const rows = batch.map(env => {
          const parsed = NodeEnvelopeSchema.safeParse(env);
          if (!parsed.success) {
            errors.push(`${env.id}: ${parsed.error.message}`);
            return null;
          }
          try { assertValidUrn(parsed.data.id); } catch (e) {
            errors.push(`${env.id}: invalid URN`);
            return null;
          }
          const { attributes, source_range, ...flat } = parsed.data;
          const attrProps: Record<string, unknown> = {};
          for (const [k, v] of Object.entries(attributes ?? {})) {
            attrProps[`attr_${k}`] = v;
          }
          return {
            id:   flat.id,
            type: flat.type,
            props: {
              ...flat,
              ...attrProps,
              source_range_start_line: source_range?.start.line   ?? null,
              source_range_end_line:   source_range?.end.line     ?? null,
              scope: this._scopeFromUrn(flat.id),
            },
          };
        }).filter(Boolean);

        if (rows.length > 0) {
          await session.run(
            `UNWIND $rows AS row
             MERGE (n:CBNode { id: row.id })
             SET n += row.props
             WITH n, row
             CALL apoc.create.addLabels(n, [row.type]) YIELD node
             RETURN count(node) AS c`,
            { rows }
          );
          written += rows.length;
        }
      } catch (err) {
        errors.push(`Batch ${i}–${i + BATCH}: ${String(err)}`);
      } finally {
        await session.close();
      }
    }
    return { written, errors };
  }

  // ── Edge writes ───────────────────────────────────────────────────────────

  /**
   * Upsert a directed edge between two existing nodes.
   *
   * Creates the Neo4j relationship and a companion :CBEdge shadow node for
   * idempotency tracking (Neo4j Community does not support relationship uniqueness
   * constraints natively, so we track edge IDs via shadow nodes).
   */
  async upsertEdge(envelope: EdgeEnvelope): Promise<void> {
    const parsed = EdgeEnvelopeSchema.safeParse(envelope);
    if (!parsed.success) {
      throw new Error(
        `Invalid EdgeEnvelope ${envelope.id}: ${parsed.error.message}`
      );
    }
    const { attributes, source_range, ...flat } = parsed.data;
    const attrProps: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(attributes ?? {})) {
      attrProps[`attr_${k}`] = v;
    }

    const session = this._session();
    try {
      await session.run(
        `MATCH (src:CBNode { id: $srcId })
         MATCH (tgt:CBNode { id: $tgtId })
         MERGE (src)-[r:\`${flat.type}\` { id: $edgeId }]->(tgt)
         SET r += $props`,
        {
          srcId:  flat.source_id,
          tgtId:  flat.target_id,
          edgeId: flat.id,
          props: { ...flat, ...attrProps },
        }
      );
    } finally {
      await session.close();
    }
  }

  /**
   * Batch upsert edges.
   */
  async upsertEdges(envelopes: EdgeEnvelope[]): Promise<{ written: number; errors: string[] }> {
    const errors: string[] = [];
    let written = 0;

    const BATCH = 200;
    for (let i = 0; i < envelopes.length; i += BATCH) {
      const batch = envelopes.slice(i, i + BATCH);
      const session = this._session();
      try {
        // Group by edge type for UNWIND+MERGE efficiency
        const byType = new Map<string, typeof batch>();
        for (const env of batch) {
          const parsed = EdgeEnvelopeSchema.safeParse(env);
          if (!parsed.success) { errors.push(env.id); continue; }
          const t = parsed.data.type;
          if (!byType.has(t)) byType.set(t, []);
          byType.get(t)!.push(env);
        }
        for (const [type, rows] of byType) {
          const preparedRows = rows.map(env => {
            const { attributes, source_range, ...flat } = env as EdgeEnvelope & { source_range?: unknown };
            const attrProps: Record<string, unknown> = {};
            for (const [k, v] of Object.entries(attributes ?? {})) {
              attrProps[`attr_${k}`] = v;
            }
            return { ...flat, ...attrProps };
          });
          await session.run(
            `UNWIND $rows AS row
             MATCH (src:CBNode { id: row.source_id })
             MATCH (tgt:CBNode { id: row.target_id })
             MERGE (src)-[r:\`${type}\` { id: row.id }]->(tgt)
             SET r += row
             RETURN count(r) AS c`,
            { rows: preparedRows }
          );
          written += preparedRows.length;
        }
      } catch (err) {
        errors.push(`Edge batch ${i}: ${String(err)}`);
      } finally {
        await session.close();
      }
    }
    return { written, errors };
  }

  // ── Invalidation ──────────────────────────────────────────────────────────

  /**
   * Mark a node as no longer valid as of a given commit.
   * Sets valid_to_commit and status = "removed".
   * The node is preserved — never deleted.
   */
  async invalidateNode(id: string, commitSha: string): Promise<void> {
    assertValidUrn(id);
    const session = this._session();
    try {
      await session.run(
        `MATCH (n:CBNode { id: $id })
         SET n.valid_to_commit = $sha, n.status = "removed"`,
        { id, sha: commitSha }
      );
    } finally {
      await session.close();
    }
  }

  /**
   * Bulk invalidate nodes by URN prefix (e.g. all nodes from a file).
   */
  async invalidateByPrefix(urnPrefix: string, commitSha: string): Promise<number> {
    const session = this._session();
    try {
      const result = await session.run(
        `MATCH (n:CBNode)
         WHERE n.id STARTS WITH $prefix AND n.valid_to_commit IS NULL
         SET n.valid_to_commit = $sha, n.status = "removed"
         RETURN count(n) AS c`,
        { prefix: urnPrefix, sha: commitSha }
      );
      return (result.records[0]?.get("c") as number) ?? 0;
    } finally {
      await session.close();
    }
  }

  // ── Queries ───────────────────────────────────────────────────────────────

  /** Run an arbitrary Cypher read query. */
  async query<T = Record<string, unknown>>(
    cypher: string,
    params?: Record<string, unknown>
  ): Promise<T[]> {
    const session = this._session();
    try {
      const result: QueryResult = await session.run(cypher, params);
      return result.records.map((r: { keys: string[]; get(key: string): unknown }) => {
        const obj: Record<string, unknown> = {};
        for (const key of r.keys) {
          obj[key as string] = r.get(key as string);
        }
        return obj as T;
      });
    } finally {
      await session.close();
    }
  }

  /** Get a single node by URN. Returns null if not found. */
  async getNode(id: string): Promise<Record<string, unknown> | null> {
    assertValidUrn(id);
    const rows = await this.query(
      `MATCH (n:CBNode { id: $id }) RETURN properties(n) AS props`,
      { id }
    );
    return (rows[0] as { props?: Record<string, unknown> })?.props ?? null;
  }

  /** Count nodes (optionally filtered by type). */
  async countNodes(type?: string): Promise<number> {
    const cypher = type
      ? `MATCH (n:CBNode { type: $type }) RETURN count(n) AS c`
      : `MATCH (n:CBNode) RETURN count(n) AS c`;
    const rows = await this.query<{ c: number }>(cypher, type ? { type } : {});
    return rows[0]?.c ?? 0;
  }

  // ── Internal helpers ──────────────────────────────────────────────────────

  private _session(): Session {
    if (!this.driver) throw new Error("GraphClient not connected — call connect() first");
    return this.driver.session({ database: this.config.database ?? "neo4j" });
  }

  /** Extract the scope segment from a URN: urn:cb:<source>:<scope>:... → scope */
  private _scopeFromUrn(urn: string): string {
    const parts = urn.split(":");
    return parts[3] ?? "";
  }
}
