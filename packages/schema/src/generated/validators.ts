// AUTO-GENERATED — edit schema.yaml and run `bun run codegen`
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
  z.literal("Organization"),
    z.literal("Repository"),
    z.literal("Branch"),
    z.literal("Commit"),
    z.literal("PullRequest"),
    z.literal("Directory"),
    z.literal("File"),
    z.literal("Module"),
    z.literal("ExternalDependency"),
    z.literal("Interface"),
    z.literal("TypeAlias"),
    z.literal("Class"),
    z.literal("Function"),
    z.literal("Method"),
    z.literal("Constant"),
    z.literal("Decorator"),
    z.literal("Route"),
    z.literal("Screen"),
    z.literal("Component"),
    z.literal("APIRoute"),
    z.literal("Layout"),
    z.literal("HTTPEndpoint"),
    z.literal("DatabaseSchema"),
    z.literal("DatabaseTable"),
    z.literal("DatabaseColumn"),
    z.literal("DatabaseIndex"),
    z.literal("DatabaseEnum"),
    z.literal("ContractDocument"),
    z.literal("ContractEndpoint"),
    z.literal("ContractRequestSchema"),
    z.literal("ContractResponseSchema"),
    z.literal("DriftSignal"),
    z.literal("PRDSection"),
    z.literal("ADR"),
    z.literal("Ticket"),
    z.literal("GlossaryTerm"),
    z.literal("NarrativeNote"),
]);

// ── EdgeType union ────────────────────────────────────────────────────────────

export const EdgeTypeSchema = z.union([
  z.literal("contains"),
    z.literal("declared_in"),
    z.literal("imports"),
    z.literal("calls"),
    z.literal("extends"),
    z.literal("implements"),
    z.literal("renders"),
    z.literal("routes_to"),
    z.literal("child_of"),
    z.literal("implemented_by"),
    z.literal("handles"),
    z.literal("calls_endpoint"),
    z.literal("reads_table"),
    z.literal("writes_table"),
    z.literal("maps_to_table"),
    z.literal("has_column"),
    z.literal("has_table"),
    z.literal("has_index"),
    z.literal("foreign_key"),
    z.literal("implements_contract"),
    z.literal("defines_endpoint"),
    z.literal("has_request_schema"),
    z.literal("has_response_schema"),
    z.literal("references_schema"),
    z.literal("authored_commit"),
    z.literal("merged_in"),
    z.literal("belongs_to_branch"),
    z.literal("documented_in"),
    z.literal("decided_in"),
    z.literal("references_ticket"),
    z.literal("embodies_concept"),
    z.literal("has_drift"),
    z.literal("signals_drift"),
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

// ── Convenience inferred types ────────────────────────────────────────────────
export type ExtractorRef = z.infer<typeof ExtractorRefSchema>;
export type SourceRange  = z.infer<typeof SourceRangeSchema>;

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
