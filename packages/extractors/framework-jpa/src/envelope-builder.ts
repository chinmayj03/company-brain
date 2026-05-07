/**
 * Builds graph envelopes from parsed JPA @Entity data.
 *
 * Confidence 0.85 — regex-based, less precise than typed Prisma schema.
 * Emits: DatabaseSchema, DatabaseTable, DatabaseColumn nodes
 *        has_table, has_column, foreign_key edges
 */

import crypto from "crypto";
import { buildUrn } from "@company-brain/schema";
import type { NodeEnvelope, EdgeEnvelope, ExtractorRef, NodeType, EdgeType } from "@company-brain/schema";
import type { JpaEntity } from "./types.js";

export interface JpaWriteBatch {
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
    confidence: 0.85,
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
    confidence: 0.85,
    valid_from_commit: commitSha,
    valid_to_commit: null,
    attributes: extraAttrs,
  };
}

/** Convert PascalCase/camelCase entity class name to snake_case table name */
function toSnakeCase(name: string): string {
  return name
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-z\d])([A-Z])/g, "$1_$2")
    .toLowerCase();
}

export function buildJpaEnvelopes(
  entities: JpaEntity[],
  scope: string,
  commitSha: string,
  extractorVersion: string,
): JpaWriteBatch {
  const nodes: NodeEnvelope[] = [];
  const edges: EdgeEnvelope[] = [];

  const extractor: ExtractorRef = { name: "framework-jpa", version: extractorVersion };
  const now = new Date().toISOString();

  // Build a lookup map: entityClassName → tableId, for FK resolution
  const entityToTableId = new Map<string, string>();
  for (const entity of entities) {
    const tableId = buildUrn({
      source: "schema",
      scope,
      artifact: `schema/jpa/tables/${entity.tableName}`,
    });
    entityToTableId.set(entity.className, tableId);
    // Also register by snake_case in case referenced entity uses class name directly
    entityToTableId.set(entity.tableName, tableId);
  }

  // ── DatabaseSchema (one per extraction run — represents the JPA layer) ────
  const schemaId = buildUrn({ source: "schema", scope, artifact: "schema/jpa" });
  nodes.push(node(
    schemaId,
    "DatabaseSchema",
    "jpa",
    scope,
    commitSha,
    extractor,
    now,
    { provider: "jpa/hibernate" },
    `${scope}:schema:jpa`,
  ));

  // ── Entities → Tables + Columns ──────────────────────────────────────────
  for (const entity of entities) {
    const tableId = buildUrn({
      source: "schema",
      scope,
      artifact: `schema/jpa/tables/${entity.tableName}`,
    });

    nodes.push(node(
      tableId,
      "DatabaseTable",
      entity.tableName,
      scope,
      commitSha,
      extractor,
      now,
      {
        schema_name: entity.schemaName || "jpa",
        db_name: entity.tableName,
        java_class: entity.className,
        source_file: entity.sourceFile,
      },
      `${scope}:jpa.${entity.tableName}`,
    ));

    edges.push(edge(schemaId, "has_table", tableId, scope, commitSha, extractor, "1-n"));

    // ── Columns ────────────────────────────────────────────────────────────
    for (const field of entity.fields) {
      const colId = buildUrn({
        source: "schema",
        scope,
        artifact: `schema/jpa/tables/${entity.tableName}/columns/${field.dbColumnName}`,
      });

      const colAttrs: Record<string, unknown> = {
        data_type: field.dbType,
        nullable: field.nullable,
        is_primary_key: field.isPrimaryKey,
        is_foreign_key: field.isForeignKey,
        db_name: field.dbColumnName,
        java_name: field.javaName,
        java_type: field.javaType,
      };
      if (field.columnLength !== null) colAttrs["length"] = field.columnLength;
      if (field.unique) colAttrs["unique"] = true;
      if (field.defaultValue !== null) colAttrs["default_value"] = field.defaultValue;
      if (field.isGeneratedValue) colAttrs["is_generated"] = true;

      nodes.push(node(
        colId,
        "DatabaseColumn",
        field.dbColumnName,
        scope,
        commitSha,
        extractor,
        now,
        colAttrs,
        `${scope}:jpa.${entity.tableName}.${field.dbColumnName}`,
      ));

      edges.push(edge(tableId, "has_column", colId, scope, commitSha, extractor, "1-n"));

      // Foreign key edge
      if (field.isForeignKey && field.referencedEntity) {
        // Try to resolve by class name first, then by snake_case table name
        const refEntitySnake = toSnakeCase(field.referencedEntity);
        const refTableId =
          entityToTableId.get(field.referencedEntity) ??
          entityToTableId.get(refEntitySnake) ??
          buildUrn({
            source: "schema",
            scope,
            artifact: `schema/jpa/tables/${refEntitySnake}`,
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
            referenced_table: refEntitySnake,
            referenced_column: field.referencedColumn ?? "id",
          },
        ));
      }
    }
  }

  return { nodes, edges };
}
