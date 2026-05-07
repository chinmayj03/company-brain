/**
 * Builds graph envelopes from parsed Prisma schema data.
 */

import crypto from "crypto";
import { buildUrn } from "@company-brain/schema";
import type { NodeEnvelope, EdgeEnvelope, ExtractorRef, NodeType, EdgeType } from "@company-brain/schema";
import type { PrismaSchema } from "./types.js";

export interface PrismaWriteBatch {
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
    confidence: 0.95,
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
    confidence: 0.95,
    valid_from_commit: commitSha,
    valid_to_commit: null,
    attributes: extraAttrs,
  };
}

export function buildPrismaEnvelopes(
  schema: PrismaSchema,
  scope: string,
  commitSha: string,
  extractorVersion: string,
): PrismaWriteBatch {
  const nodes: NodeEnvelope[] = [];
  const edges: EdgeEnvelope[] = [];

  const extractor: ExtractorRef = { name: "framework-prisma", version: extractorVersion };
  const now = new Date().toISOString();
  const stem = schema.filenameStem;

  // ── DatabaseSchema ────────────────────────────────────────────────────────
  const schemaId = buildUrn({ source: "schema", scope, artifact: `schema/${stem}` });
  nodes.push(node(schemaId, "DatabaseSchema", stem, scope, commitSha, extractor, now, {
    provider: schema.provider ?? "unknown",
    file_path: schema.filePath,
  }, `${scope}:schema:${stem}`));

  // ── Models ────────────────────────────────────────────────────────────────
  for (const model of schema.models) {
    const tableId = buildUrn({ source: "schema", scope, artifact: `schema/${stem}/tables/${model.name}` });

    nodes.push(node(tableId, "DatabaseTable", model.name, scope, commitSha, extractor, now, {
      schema_name: stem,
      db_name: model.dbName ?? model.name,
    }, `${scope}:${stem}.${model.name}`));

    edges.push(edge(schemaId, "has_table", tableId, scope, commitSha, extractor, "1-n"));

    // ── Columns ─────────────────────────────────────────────────────────────
    for (const field of model.fields) {
      if (field.isArray && field.relatedModel) continue; // skip back-relation arrays

      const colId = buildUrn({
        source: "schema",
        scope,
        artifact: `schema/${stem}/tables/${model.name}/columns/${field.name}`,
      });

      const colAttrs: Record<string, unknown> = {
        data_type: field.type,
        nullable: field.isOptional,
        is_primary_key: field.isPrimaryKey,
        is_foreign_key: field.isForeignKey,
        db_name: field.dbName ?? field.name,
      };
      if (field.defaultValue !== null) colAttrs["default_value"] = field.defaultValue;

      nodes.push(node(colId, "DatabaseColumn", field.name, scope, commitSha, extractor, now,
        colAttrs, `${scope}:${stem}.${model.name}.${field.name}`));

      edges.push(edge(tableId, "has_column", colId, scope, commitSha, extractor, "1-n"));

      if (field.isForeignKey && field.relatedModel) {
        const refTableId = buildUrn({
          source: "schema",
          scope,
          artifact: `schema/${stem}/tables/${field.relatedModel}`,
        });
        edges.push(edge(colId, "foreign_key", refTableId, scope, commitSha, extractor, "n-1", {
          referenced_field: "id",
        }));
      }
    }

    // ── Indexes ──────────────────────────────────────────────────────────────
    for (const idx of model.indexes) {
      const idxName = idx.name ?? `${model.name}_${idx.fields.join("_")}_${idx.kind}`;
      const idxId = buildUrn({
        source: "schema",
        scope,
        artifact: `schema/${stem}/tables/${model.name}/indexes/${idxName}`,
      });

      nodes.push(node(idxId, "DatabaseIndex", idxName, scope, commitSha, extractor, now, {
        fields: idx.fields,
        is_unique: idx.isUnique,
        index_type: idx.kind,
      }, `${scope}:${stem}.${model.name}.idx.${idxName}`));

      edges.push(edge(tableId, "has_index", idxId, scope, commitSha, extractor, "1-n"));
    }
  }

  // ── Enums ─────────────────────────────────────────────────────────────────
  for (const en of schema.enums) {
    const enumId = buildUrn({ source: "schema", scope, artifact: `schema/${stem}/enums/${en.name}` });

    nodes.push(node(enumId, "DatabaseEnum", en.name, scope, commitSha, extractor, now, {
      values: en.values,
    }, `${scope}:${stem}.enum.${en.name}`));
  }

  return { nodes, edges };
}
