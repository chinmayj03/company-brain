export { CoreTsExtractor }           from "./extractor.js";
export { discoverFiles }             from "./extractor.js";
export { runTreeSitterPass, detectLanguage } from "./passes/tree-sitter-pass.js";
export { buildEnvelopes, resolveImportPath } from "./passes/envelope-builder.js";
export type {
  ExtractorManifest,
  ExtractorContext,
  ExtractorResult,
} from "./extractor.js";
export type {
  FilePassResult,
  ExtractedFile,
  ExtractedSymbol,
  ExtractedImport,
  ExtractedCallSite,
  WriteBatch,
} from "./types.js";
