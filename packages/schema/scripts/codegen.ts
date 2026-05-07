#!/usr/bin/env bun
/**
 * packages/schema/scripts/codegen.ts
 *
 * Generates TypeScript types and Zod validators from schema.yaml.
 * Run: bun run codegen  (from packages/schema/)
 *
 * Outputs:
 *   src/generated/node-types.ts   — NodeType enum, isNodeType guard
 *   src/generated/edge-types.ts   — EdgeType enum, isEdgeType guard
 *   src/generated/validators.ts   — Zod schemas for NodeEnvelope + EdgeEnvelope
 */

import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { join } from "path";
import yaml from "js-yaml";

const ROOT = new URL("..", import.meta.url).pathname;
const SCHEMA_PATH = join(ROOT, "schema.yaml");
const GEN_DIR = join(ROOT, "src", "generated");

interface AttributeDef {
  name: string;
  type: "string" | "number" | "boolean" | "string[]";
  required: boolean;
}

interface NodeTypeDef {
  name: string;
  description: string;
  attributes: AttributeDef[];
  extractor: string;
}

interface EdgeTypeDef {
  name: string;
  description: string;
  from: string[];
  to: string[];
  cardinality: "1-1" | "1-n" | "n-1" | "n-n";
  attributes?: AttributeDef[];
}

interface Schema {
  schema_version: string;
  node_types: NodeTypeDef[];
  edge_types: EdgeTypeDef[];
}

function tsAttrType(attr: AttributeDef): string {
  const base =
    attr.type === "string[]" ? "string[]" :
    attr.type === "number"   ? "number" :
    attr.type === "boolean"  ? "boolean" :
    "string";
  return attr.required ? base : `${base} | undefined`;
}

function zodAttrType(attr: AttributeDef): string {
  const base =
    attr.type === "string[]" ? "z.array(z.string())" :
    attr.type === "number"   ? "z.number()" :
    attr.type === "boolean"  ? "z.boolean()" :
    "z.string()";
  return attr.required ? base : `${base}.optional()`;
}

function generateNodeTypes(types: NodeTypeDef[]): string {
  const names = types.map(t => `"${t.name}"`).join(" |\n  ");
  const values = types.map(t => `  ${t.name} = "${t.name}",`).join("\n");

  const attrInterfaces = types.map(t => {
    if (!t.attributes || t.attributes.length === 0) return "";
    const fields = t.attributes.map(a =>
      `  /** ${a.name} */\n  ${a.name}${a.required ? "" : "?"}: ${tsAttrType(a)};`
    ).join("\n");
    return `\nexport interface ${t.name}Attributes {\n${fields}\n}`;
  }).filter(Boolean).join("\n");

  return `// AUTO-GENERATED — edit schema.yaml and run \`bun run codegen\`
// Schema version: generated from packages/schema/schema.yaml

export type NodeType =
  ${names};

export enum NodeTypeEnum {
${values}
}

export const NODE_TYPE_VALUES = new Set<NodeType>([
${types.map(t => `  "${t.name}",`).join("\n")}
]);

export function isNodeType(value: unknown): value is NodeType {
  return typeof value === "string" && NODE_TYPE_VALUES.has(value as NodeType);
}

export function assertNodeType(value: unknown): asserts value is NodeType {
  if (!isNodeType(value)) {
    throw new Error(\`Invalid NodeType: \${JSON.stringify(value)}\`);
  }
}

/** Extractor that populates each node type (informational). */
export const NODE_TYPE_EXTRACTOR: Record<NodeType, string> = {
${types.map(t => `  "${t.name}": "${t.extractor ?? "unknown"}",`).join("\n")}
};
${attrInterfaces}
`;
}

function generateEdgeTypes(types: EdgeTypeDef[]): string {
  const names = types.map(t => `"${t.name}"`).join(" |\n  ");
  const values = types.map(t => `  ${t.name} = "${t.name}",`).join("\n");

  const descriptions = types.map(t =>
    `  "${t.name}": "${t.description?.replace(/"/g, '\\"') ?? ""}",`
  ).join("\n");

  return `// AUTO-GENERATED — edit schema.yaml and run \`bun run codegen\`

export type EdgeType =
  ${names};

export enum EdgeTypeEnum {
${values}
}

export const EDGE_TYPE_VALUES = new Set<EdgeType>([
${types.map(t => `  "${t.name}",`).join("\n")}
]);

export function isEdgeType(value: unknown): value is EdgeType {
  return typeof value === "string" && EDGE_TYPE_VALUES.has(value as EdgeType);
}

export const EDGE_TYPE_DESCRIPTIONS: Record<EdgeType, string> = {
${descriptions}
};
`;
}

function generateValidators(
  schema: Schema,
  nodeTypes: NodeTypeDef[],
  edgeTypes: EdgeTypeDef[]
): string {
  const nodeTypeUnion = nodeTypes.map(t => `z.literal("${t.name}")`).join(",\n    ");
  const edgeTypeUnion = edgeTypes.map(t => `z.literal("${t.name}")`).join(",\n    ");

  return `// AUTO-GENERATED — edit schema.yaml and run \`bun run codegen\`
import { z } from "zod";

// ── Common sub-schemas ────────────────────────────────────────────────────────

export const SourceRangeSchema = z.object({
  start: z.object({ line: z.number(), column: z.number(), offset: z.number() }),
  end:   z.object({ line: z.number(), column: z.number(), offset: z.number() }),
});

export const ExtractorRefSchema = z.object({
  name:    z.string(),
  version: z.string(),
});

export const DerivationSchema = z.enum([
  "ast", "lsp", "config", "framework_parser",
  "static_analysis", "llm_with_evidence", "llm_inference_only",
  "human", "api",
]);

export const StatusSchema = z.enum([
  "active", "deprecated", "removed", "planned", "draft",
]);

// ── NodeType union ────────────────────────────────────────────────────────────

export const NodeTypeSchema = z.union([
  ${nodeTypeUnion},
]);

// ── EdgeType union ────────────────────────────────────────────────────────────

export const EdgeTypeSchema = z.union([
  ${edgeTypeUnion},
]);

// ── NodeEnvelope ──────────────────────────────────────────────────────────────

export const NodeEnvelopeSchema = z.object({
  // Identity
  id:              z.string().min(1),
  type:            NodeTypeSchema,
  name:            z.string().min(1),
  qualified_name:  z.string().optional(),
  aliases:         z.array(z.string()).optional(),

  // Provenance — ALL required; GraphClient throws if missing
  source_uri:         z.string(),
  source_range:       SourceRangeSchema.optional(),
  source_checksum:    z.string(),
  extractor:          ExtractorRefSchema,
  extraction_timestamp: z.string(),  // ISO-8601
  confidence:         z.number().min(0).max(1),
  derivation:         DerivationSchema,

  // Temporal validity
  created_at_commit:       z.string(),
  last_modified_commit:    z.string(),
  valid_from_commit:       z.string(),
  valid_to_commit:         z.string().nullable(),

  // Lifecycle
  status:              StatusSchema,
  deprecated_in_commit: z.string().optional(),
  removed_in_commit:    z.string().optional(),

  // Cross-cutting
  tags:           z.array(z.string()).optional(),
  raw_payload:    z.unknown().optional(),
  attributes:     z.record(z.string(), z.unknown()),
});

export type NodeEnvelope = z.infer<typeof NodeEnvelopeSchema>;

// ── EdgeEnvelope ──────────────────────────────────────────────────────────────

export const EdgeEnvelopeSchema = z.object({
  id:         z.string().min(1),
  type:       EdgeTypeSchema,
  source_id:  z.string().min(1),
  target_id:  z.string().min(1),
  cardinality: z.enum(["1-1", "1-n", "n-1", "n-n"]),

  // Provenance
  source_uri:   z.string(),
  source_range: SourceRangeSchema.optional(),
  extractor:    ExtractorRefSchema,
  derivation:   DerivationSchema,
  confidence:   z.number().min(0).max(1),

  // Temporal
  valid_from_commit: z.string(),
  valid_to_commit:   z.string().nullable(),

  // Edge-specific
  attributes: z.record(z.string(), z.unknown()),
});

export type EdgeEnvelope = z.infer<typeof EdgeEnvelopeSchema>;
`;
}

// ── Main ──────────────────────────────────────────────────────────────────────

mkdirSync(GEN_DIR, { recursive: true });

const raw = readFileSync(SCHEMA_PATH, "utf8");
const schema = yaml.load(raw) as Schema;

const nodeTypesTs = generateNodeTypes(schema.node_types);
const edgeTypesTs = generateEdgeTypes(schema.edge_types);
const validatorsTs = generateValidators(schema, schema.node_types, schema.edge_types);

writeFileSync(join(GEN_DIR, "node-types.ts"), nodeTypesTs);
writeFileSync(join(GEN_DIR, "edge-types.ts"), edgeTypesTs);
writeFileSync(join(GEN_DIR, "validators.ts"), validatorsTs);

console.log(`✅ Codegen complete (schema v${schema.schema_version})`);
console.log(`   Node types: ${schema.node_types.length}`);
console.log(`   Edge types: ${schema.edge_types.length}`);
console.log(`   Output: ${GEN_DIR}/`);
