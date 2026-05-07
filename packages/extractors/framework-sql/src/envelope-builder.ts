/**
 * Builds graph envelopes from parsed SQL DDL data.
 *
 * Follows the same pattern as framework-prisma/envelope-builder.ts.
 * Confidence: 0.90 (slightly lower than Prisma's 0.95 — SQL parsing is
 * less precise than a typed schema file).
 */

import crypto from "crypto";
import { buildUrn } from "@company-brain/schema";
import type { NodeEnvelope, EdgeEnvelope, ExtractorRef, NodeType, EdgeType } from "@company-brain/schema";
import type { SqlTable } from "./types.js";

export interface SqlWriteBatch {
  nodes: NodeEnvelope[];
  edges: EdgeEnvelope[];
}

const CONFIDENCE = 0.90;

function md5(input: string): string {
  return crypto.createHash("md5").update(input).digest("hex");
}

function node(
  id: string,
  type: NodeType,
  name: string,
  scope: string,
  commitSha: string,
  extractor: ExtractorRef,
  now: string,
  attributes: Record<string, unknown>,
  qualifiedName?: string,
): NodeEnvelope {
  return {
    id,
    type,
    name,
    ...(qualifiedName ? { qualified_name: qualifiedName } : {}),
    source_uri: `urn:cb:schema:${scope}`,
    source_checksum: md5(id),
    extractor,
    extraction_timestamp: now,
    confidence: CONFIDENCE,
    derivation: "config",
    created_at_commit: commitSha,
    last_modified_commit: commitSha,
    valid_from_commit: commitSha,
    valid_to_commit: null,
    status: "active",
    attributes,
  };
}

function edge(
  fromId: string,
  type: EdgeType,
  toId: string,
  scope: string,
  commitSha: string,
  extractor: ExtractorRef,
  cardinality: "1-1" | "1-n" | "n-1" | "n-n",
  extraAttrs: Record<string, unknown> = {},
): EdgeEnvelope {
  return {
    id: `${fromId}>>${type}>>${toId}`,
    type,
    source_id: fromId,
    target_id: toId,
    cardinality,
    source_uri: `urn:cb:schema:${scope}`,
    extractor,
    derivation: "config",
    confidence: CONFIDENCE,
    valid_from_commit: commitSha,
    valid_to_commit: null,
    attributes: extraAttrs,
  };
}

/**
 * Build a stable schema stem from a source file path.
 * e.g. "src/main/resources/db/migration/V1__create_graph_tables.sql" → "V1__create_graph_tables"
 */
function fileToStem(sourceFile: string): string {
  return sourceFile
    .split("/")
    .pop()!
    .replace(/\.sql$/i, "");
}

export function buildSqlEnvelopes(
  tables: SqlTable[],
  scope: string,
  commitSha: string,
  sourceFile: string,
  extractorVersion: string,
): SqlWriteBatch {
  const nodes: NodeEnvelope[] = [];
  const edges: EdgeEnvelope[] = [];

  const extractor: ExtractorRef = { name: "framework-sql", version: extractorVersion };
  const now = new Date().toISOString();
  const stem = fileToStem(sourceFile);

  // ── DatabaseSchema ────────────────────────────────────────────────────────
  const schemaId = buildUrn({ source: "schema", scope, artifact: `schemas/${sourceFile}` });
  nodes.push(
    node(schemaId, "DatabaseSchema", stem, scope, commitSha, extractor, now, {
      provider: "sql",
      file_path: sourceFile,
    }, `${scope}:schema:${stem}`),
  );

  // ── Tables ────────────────────────────────────────────────────────────────
  for (const table of tables) {
    const tableId = buildUrn({
      source: "schema",
      scope,
      artifact: `schemas/${sourceFile}/tables/${table.name}`,
    });

    nodes.push(
      node(tableId, "DatabaseTable", table.name, scope, commitSha, extractor, now, {
        schema_name: table.schemaName || stem,
        db_name: table.name,
        source_file: sourceFile,
      }, `${scope}:${stem}.${table.name}`),
    );

    edges.push(edge(schemaId, "has_table", tableId, scope, commitSha, extractor, "1-n"));

    // ── Columns ───────────────────────────────────────────────────────────
    for (const col of table.columns) {
      const colId = buildUrn({
        source: "schema",
        scope,
        artifact: `schemas/${sourceFile}/tables/${table.name}/columns/${col.name}`,
      });

      const colAttrs: Record<string, unknown> = {
        data_type: col.dataType,
        raw_type: col.rawType,
        nullable: col.nullable,
        is_primary_key: col.isPrimaryKey,
        is_foreign_key: col.isForeignKey,
        db_name: col.dbName,
      };
      if (col.defaultValue !== null) colAttrs["default_value"] = col.defaultValue;

      nodes.push(
        node(colId, "DatabaseColumn", col.name, scope, commitSha, extractor, now,
          colAttrs, `${scope}:${stem}.${table.name}.${col.name}`),
      );

      edges.push(edge(tableId, "has_column", colId, scope, commitSha, extractor, "1-n"));

      // Inline foreign key edge (column → referenced table)
      if (col.isForeignKey) {
        // Find the FK definition for this column
        const fk = table.foreignKeys.find((f) => f.fromColumn === col.name);
        if (fk) {
          const refTableId = buildUrn({
            source: "schema",
            scope,
            artifact: `schemas/${sourceFile}/tables/${fk.toTable}`,
          });
          edges.push(
            edge(colId, "foreign_key", refTableId, scope, commitSha, extractor, "n-1", {
              referenced_field: fk.toColumn,
              on_delete: fk.onDelete,
            }),
          );
        }
      }
    }

    // ── Indexes ──────────────────────────────────────────────────────────
    for (const idx of table.indexes) {
      const idxId = buildUrn({
        source: "schema",
        scope,
        artifact: `schemas/${sourceFile}/tables/${table.name}/indexes/${idx.name}`,
      });

      nodes.push(
        node(idxId, "DatabaseIndex", idx.name, scope, commitSha, extractor, now, {
          columns: idx.columns,
          is_unique: idx.unique,
        }, `${scope}:${stem}.${table.name}.idx.${idx.name}`),
      );

      edges.push(edge(tableId, "has_index", idxId, scope, commitSha, extractor, "1-n"));
    }
  }

  return { nodes, edges };
}
