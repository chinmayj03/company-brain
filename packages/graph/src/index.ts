/**
 * packages/graph/src/index.ts
 * Public API for @company-brain/graph.
 */
export { GraphClient, type GraphClientConfig } from "./client.js";
export type {
  ExtractorPlugin,
  ExtractorManifest,
  ExtractorContext,
  ExtractorResult,
  ExtractorLog,
} from "./extractor-types.js";
