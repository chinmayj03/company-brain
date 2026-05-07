// AUTO-GENERATED — edit schema.yaml and run `bun run codegen`

export type EdgeType =
  "contains" |
  "declared_in" |
  "imports" |
  "calls" |
  "extends" |
  "implements" |
  "renders" |
  "routes_to" |
  "child_of" |
  "implemented_by" |
  "handles" |
  "calls_endpoint" |
  "reads_table" |
  "writes_table" |
  "maps_to_table" |
  "has_column" |
  "has_table" |
  "has_index" |
  "foreign_key" |
  "implements_contract" |
  "defines_endpoint" |
  "has_request_schema" |
  "has_response_schema" |
  "references_schema" |
  "authored_commit" |
  "merged_in" |
  "belongs_to_branch" |
  "documented_in" |
  "decided_in" |
  "references_ticket" |
  "embodies_concept" |
  "has_drift" |
  "signals_drift";

export enum EdgeTypeEnum {
  contains = "contains",
  declared_in = "declared_in",
  imports = "imports",
  calls = "calls",
  extends = "extends",
  implements = "implements",
  renders = "renders",
  routes_to = "routes_to",
  child_of = "child_of",
  implemented_by = "implemented_by",
  handles = "handles",
  calls_endpoint = "calls_endpoint",
  reads_table = "reads_table",
  writes_table = "writes_table",
  maps_to_table = "maps_to_table",
  has_column = "has_column",
  has_table = "has_table",
  has_index = "has_index",
  foreign_key = "foreign_key",
  implements_contract = "implements_contract",
  defines_endpoint = "defines_endpoint",
  has_request_schema = "has_request_schema",
  has_response_schema = "has_response_schema",
  references_schema = "references_schema",
  authored_commit = "authored_commit",
  merged_in = "merged_in",
  belongs_to_branch = "belongs_to_branch",
  documented_in = "documented_in",
  decided_in = "decided_in",
  references_ticket = "references_ticket",
  embodies_concept = "embodies_concept",
  has_drift = "has_drift",
  signals_drift = "signals_drift",
}

export const EDGE_TYPE_VALUES = new Set<EdgeType>([
  "contains",
  "declared_in",
  "imports",
  "calls",
  "extends",
  "implements",
  "renders",
  "routes_to",
  "child_of",
  "implemented_by",
  "handles",
  "calls_endpoint",
  "reads_table",
  "writes_table",
  "maps_to_table",
  "has_column",
  "has_table",
  "has_index",
  "foreign_key",
  "implements_contract",
  "defines_endpoint",
  "has_request_schema",
  "has_response_schema",
  "references_schema",
  "authored_commit",
  "merged_in",
  "belongs_to_branch",
  "documented_in",
  "decided_in",
  "references_ticket",
  "embodies_concept",
  "has_drift",
  "signals_drift",
]);

export function isEdgeType(value: unknown): value is EdgeType {
  return typeof value === "string" && EDGE_TYPE_VALUES.has(value as EdgeType);
}

export const EDGE_TYPE_DESCRIPTIONS: Record<EdgeType, string> = {
  "contains": "Parent contains child (directory→file, file→module, class→method).",
  "declared_in": "Symbol is declared in file.",
  "imports": "Source imports from target.",
  "calls": "Caller invokes callee.",
  "extends": "Class extends / interface extends interface.",
  "implements": "Class implements interface.",
  "renders": "Screen renders component.",
  "routes_to": "Route resolves to screen.",
  "child_of": "Layout or screen is nested under a parent layout.",
  "implemented_by": "Framework node (APIRoute, Screen, DatabaseTable) is implemented by a code symbol.",
  "handles": "Function/method handles HTTP endpoint.",
  "calls_endpoint": "Client code calls an HTTP endpoint.",
  "reads_table": "Function/method reads from database table.",
  "writes_table": "Function/method writes to database table.",
  "maps_to_table": "ORM class maps to database table.",
  "has_column": "Table has column.",
  "has_table": "Schema contains table.",
  "has_index": "Table has index.",
  "foreign_key": "Column references another table's column (foreign key constraint).",
  "implements_contract": "Endpoint implements a contract operation.",
  "defines_endpoint": "Contract document defines an endpoint operation.",
  "has_request_schema": "Contract endpoint has a request body schema.",
  "has_response_schema": "Contract endpoint has a response schema for a status code.",
  "references_schema": "Schema node references another schema via $ref.",
  "authored_commit": "Person authored commit.",
  "merged_in": "PR merged a set of commits.",
  "belongs_to_branch": "Commit belongs to branch.",
  "documented_in": "Node is documented in a PRD section or ADR.",
  "decided_in": "Architectural decision governs a code node.",
  "references_ticket": "PR or commit references a ticket.",
  "embodies_concept": "Code node embodies a domain concept.",
  "has_drift": "Node has a detected drift signal vs. documentation.",
  "signals_drift": "DriftSignal points to the implementation or contract node where drift was detected.",
};
