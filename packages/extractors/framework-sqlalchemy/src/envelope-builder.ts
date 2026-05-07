/**
 * Builds graph envelopes from parsed Python ORM model data.
 *
 * Confidence 0.80 — Python regex parsing is heuristic.
 * Emits: DatabaseSchema, DatabaseTable, DatabaseColumn nodes
 *        has_table, has_column, foreign_key edges
 */

import crypto from "crypto";
import { buildUrn } from "@company-brain/schema";
import type { NodeEnvelope, EdgeEnvelope, ExtractorRef, NodeType, EdgeType } from "@company-brain/schema";
import type { PythonOrmModel } from "./types.js";

export interface SqlAlchemyWriteBatch {
  nodes: NodeEnvelope[];
  edges: EdgeEnvelope[];
}

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
    confidence: 0.80,
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
    confidence: 0.80,
    valid_from_commit: commitSha,
    valid_to_commit: null,
    attributes: extraAttrs,
  };
}

export function buildSqlAlchemyEnvelopes(
  models: PythonOrmModel[],
  scope: string,
  commitSha: string,
  extractorVersion: string,
): SqlAlchemyWriteBatch {
  const nodes: NodeEnvelope[] = [];
  const edges: EdgeEnvelope[] = [];

  const extractor: ExtractorRef = { name: "framework-sqlalchemy", version: extractorVersion };
  const now = new Date().toISOString();

  // Determine the ORM label for the schema artifact
  // Use one schema node per ORM type found; if mixed, group under "python-orm"
  const ormTypes = new Set(models.map((m) => m.orm));
  const ormLabel = ormTypes.size === 1 ? [...ormTypes][0] : "python-orm";

  // ── DatabaseSchema ────────────────────────────────────────────────────────
  const schemaId = buildUrn({ source: "schema", scope, artifact: `schema/${ormLabel}` });
  nodes.push(node(
    schemaId,
    "DatabaseSchema",
    ormLabel,
    scope,
    commitSha,
    extractor,
    now,
    { provider: ormLabel },
    `${scope}:schema:${ormLabel}`,
  ));

  // ── Models → Tables + Columns ─────────────────────────────────────────────
  for (const model of models) {
    const tableId = buildUrn({
      source: "schema",
      scope,
      artifact: `schema/${ormLabel}/tables/${model.tableName}`,
    });

    nodes.push(node(
      tableId,
      "DatabaseTable",
      model.tableName,
      scope,
      commitSha,
      extractor,
      now,
      {
        schema_name: ormLabel,
        db_name: model.tableName,
        python_class: model.className,
        orm: model.orm,
        source_file: model.sourceFile,
      },
      `${scope}:${ormLabel}.${model.tableName}`,
    ));

    edges.push(edge(schemaId, "has_table", tableId, scope, commitSha, extractor, "1-n"));

    // ── Columns ────────────────────────────────────────────────────────────
    for (const field of model.fields) {
      const colId = buildUrn({
        source: "schema",
        scope,
        artifact: `schema/${ormLabel}/tables/${model.tableName}/columns/${field.dbColumnName}`,
      });

      const colAttrs: Record<string, unknown> = {
        data_type: field.dbType,
        nullable: field.nullable,
        is_primary_key: field.isPrimaryKey,
        is_foreign_key: field.isForeignKey,
        db_name: field.dbColumnName,
        python_name: field.name,
      };
      if (field.unique) colAttrs["unique"] = true;
      if (field.serverDefault !== null) colAttrs["server_default"] = field.serverDefault;
      if (field.foreignKeyTarget !== null) colAttrs["fk_target"] = field.foreignKeyTarget;

      nodes.push(node(
        colId,
        "DatabaseColumn",
        field.dbColumnName,
        scope,
        commitSha,
        extractor,
        now,
        colAttrs,
        `${scope}:${ormLabel}.${model.tableName}.${field.dbColumnName}`,
      ));

      edges.push(edge(tableId, "has_column", colId, scope, commitSha, extractor, "1-n"));

      // Foreign key edge
      if (field.isForeignKey && field.foreignKeyTarget) {
        // foreignKeyTarget may be "other_table.id" or just "other_table"
        const parts = field.foreignKeyTarget.split(".");
        const refTable = parts[0];
        const refColumn = parts[1] ?? "id";

        const refTableId = buildUrn({
          source: "schema",
          scope,
          artifact: `schema/${ormLabel}/tables/${refTable}`,
        });

        edges.push(edge(
          colId,
          "foreign_key",
          refTableId,
          scope,
          commitSha,
          extractor,
          "n-1",
          {
            referenced_table: refTable,
            referenced_column: refColumn,
          },
        ));
      }
    }
  }

  return { nodes, edges };
}
