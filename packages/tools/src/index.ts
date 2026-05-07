export { createToolRouter, type ToolRouter } from "./router.js";
export { success, absent, toolResultSchema, type ToolResult, type ToolSuccess, type ToolAbsence, type AbsenceReason } from "./contract.js";

// Symbol queries
export { findSymbol, findCallers, findCallees, getFunctionSignature } from "./queries/symbols.js";
export type { SymbolRecord, FunctionSignature, FindSymbolInput, FindCallersInput, FindCalleesInput } from "./queries/symbols.js";

// File queries
export { getFileSummary, listFilesIn } from "./queries/files.js";
export type { FileSummary, FileRecord, GetFileSummaryInput, ListFilesInInput } from "./queries/files.js";

// Contract + drift queries
export { getContractForEndpoint, listEndpointsImplementingContract, getDriftSignals } from "./queries/contracts.js";
export type {
  ContractEndpointRecord, DriftSignalRecord, EndpointImplementationRecord,
  GetContractForEndpointInput, ListEndpointsImplementingContractInput, GetDriftSignalsInput,
} from "./queries/contracts.js";

// Database queries
export { getTableForEntity, findColumnsWithPattern, getForeignKeys } from "./queries/database.js";
export type {
  TableRecord, ColumnRecord, ColumnSearchRecord, ForeignKeyRecord,
  GetTableForEntityInput, FindColumnsWithPatternInput, GetForeignKeysInput,
} from "./queries/database.js";

// Hybrid queries
export { JavaApiClient, hybridBlastRadius, hybridFindSymbol, hybridGetNodeContext } from "./queries/hybrid.js";
export type {
  HybridBlastRadiusNode, HybridBlastRadiusResult, HybridBlastRadiusInput,
  HybridNodeContext, HybridNodeContextFacts, HybridSemanticContext, HybridGetNodeContextInput,
} from "./queries/hybrid.js";
