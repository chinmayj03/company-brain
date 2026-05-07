/**
 * packages/schema/src/index.ts
 *
 * Public API for @company-brain/schema.
 *
 * Consumers import from this barrel — never from sub-paths directly.
 */

// URN identity scheme
export {
  buildUrn,
  parseUrn,
  assertValidUrn,
  isValidUrn,
  Urn,
  type UrnSource,
  type UrnParts,
} from "./urn.js";

// Generated types (produced by `bun run codegen`)
export {
  type NodeType,
  NodeTypeEnum,
  NODE_TYPE_VALUES,
  isNodeType,
  assertNodeType,
  NODE_TYPE_EXTRACTOR,
} from "./generated/node-types.js";

export {
  type EdgeType,
  EdgeTypeEnum,
  EDGE_TYPE_VALUES,
  isEdgeType,
  EDGE_TYPE_DESCRIPTIONS,
} from "./generated/edge-types.js";

// Zod validators + inferred TypeScript types
export {
  SourceRangeSchema,
  ExtractorRefSchema,
  DerivationSchema,
  StatusSchema,
  NodeTypeSchema,
  EdgeTypeSchema,
  NodeEnvelopeSchema,
  EdgeEnvelopeSchema,
  type NodeEnvelope,
  type EdgeEnvelope,
  type ExtractorRef,
  type SourceRange,
} from "./generated/validators.js";
