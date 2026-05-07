/**
 * Builds graph envelopes from a parsed OpenAPI document.
 */

import crypto from "crypto";
import { buildUrn } from "@company-brain/schema";
import type { NodeEnvelope, EdgeEnvelope, ExtractorRef, NodeType, EdgeType } from "@company-brain/schema";
import type { ExtractedOpenApiDoc } from "./types.js";

export interface OpenApiWriteBatch {
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
    source_uri: `urn:cb:contract:${scope}`,
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
): EdgeEnvelope {
  return {
    id: `${fromId}>>${type}>>${toId}`,
    type,
    source_id: fromId,
    target_id: toId,
    cardinality,
    source_uri: `urn:cb:contract:${scope}`,
    extractor,
    derivation: "config",
    confidence: 0.95,
    valid_from_commit: commitSha,
    valid_to_commit: null,
    attributes: {},
  };
}

export function buildOpenApiEnvelopes(
  doc: ExtractedOpenApiDoc,
  scope: string,
  commitSha: string,
  extractorVersion: string,
): OpenApiWriteBatch {
  const nodes: NodeEnvelope[] = [];
  const edges: EdgeEnvelope[] = [];

  const extractor: ExtractorRef = { name: "framework-openapi", version: extractorVersion };
  const now = new Date().toISOString();
  const stem = doc.filenameStem;

  // ── ContractDocument ──────────────────────────────────────────────────────
  const docId = buildUrn({ source: "contract", scope, artifact: `contracts/${stem}` });
  nodes.push(node(docId, "ContractDocument", doc.title ?? stem, scope, commitSha, extractor, now, {
    format: doc.format,
    spec_version: doc.specVersion ?? "unknown",
    title: doc.title ?? stem,
    file_path: doc.filePath,
  }, `${scope}:contract:${stem}`));

  // ── Operations ────────────────────────────────────────────────────────────
  for (const op of doc.operations) {
    const endpointId = buildUrn({
      source: "contract",
      scope,
      artifact: `contracts/${stem}/operations/${op.operationId}`,
    });

    const endpointAttrs: Record<string, unknown> = {
      http_method: op.method,
      path: op.path,
      operation_id: op.operationId,
      tags: op.tags,
      deprecated: op.deprecated,
    };
    if (op.summary) endpointAttrs["summary"] = op.summary;

    nodes.push(node(
      endpointId, "ContractEndpoint",
      `${op.method} ${op.path}`,
      scope, commitSha, extractor, now,
      endpointAttrs,
      `${scope}:${stem}:${op.operationId}`,
    ));

    edges.push(edge(docId, "defines_endpoint", endpointId, scope, commitSha, extractor, "1-n"));

    // ── Request schema ────────────────────────────────────────────────────
    if (op.requestBody) {
      const reqId = buildUrn({
        source: "contract",
        scope,
        artifact: `contracts/${stem}/operations/${op.operationId}/request`,
      });
      nodes.push(node(reqId, "ContractRequestSchema",
        `${op.operationId}:request`,
        scope, commitSha, extractor, now, {
          content_type: op.requestBody.contentType,
          schema_json: JSON.stringify(op.requestBody.schema),
          required: true,
        }, `${scope}:${stem}:${op.operationId}:request`,
      ));
      edges.push(edge(endpointId, "has_request_schema", reqId, scope, commitSha, extractor, "1-1"));
    }

    // ── Response schemas ──────────────────────────────────────────────────
    for (const response of op.responses) {
      const primaryMedia = response.mediaTypes[0];
      const respId = buildUrn({
        source: "contract",
        scope,
        artifact: `contracts/${stem}/operations/${op.operationId}/responses/${response.statusCode}`,
      });

      const respAttrs: Record<string, unknown> = {
        status_code: response.statusCode,
        content_type: primaryMedia?.contentType ?? "application/json",
        schema_json: primaryMedia ? JSON.stringify(primaryMedia.schema) : null,
      };
      if (response.description) respAttrs["description"] = response.description;

      nodes.push(node(
        respId, "ContractResponseSchema",
        `${op.operationId}:${response.statusCode}`,
        scope, commitSha, extractor, now,
        respAttrs,
        `${scope}:${stem}:${op.operationId}:${response.statusCode}`,
      ));
      edges.push(edge(endpointId, "has_response_schema", respId, scope, commitSha, extractor, "1-n"));
    }
  }

  return { nodes, edges };
}
