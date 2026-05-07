// AUTO-GENERATED — edit schema.yaml and run `bun run codegen`
// Schema version: generated from packages/schema/schema.yaml

export type NodeType =
  "Organization" |
  "Repository" |
  "Branch" |
  "Commit" |
  "PullRequest" |
  "Directory" |
  "File" |
  "Module" |
  "ExternalDependency" |
  "Interface" |
  "TypeAlias" |
  "Class" |
  "Function" |
  "Method" |
  "Constant" |
  "Decorator" |
  "Route" |
  "Screen" |
  "Component" |
  "APIRoute" |
  "Layout" |
  "HTTPEndpoint" |
  "DatabaseSchema" |
  "DatabaseTable" |
  "DatabaseColumn" |
  "DatabaseIndex" |
  "DatabaseEnum" |
  "ContractDocument" |
  "ContractEndpoint" |
  "ContractRequestSchema" |
  "ContractResponseSchema" |
  "DriftSignal" |
  "PRDSection" |
  "ADR" |
  "Ticket" |
  "GlossaryTerm" |
  "NarrativeNote";

export enum NodeTypeEnum {
  Organization = "Organization",
  Repository = "Repository",
  Branch = "Branch",
  Commit = "Commit",
  PullRequest = "PullRequest",
  Directory = "Directory",
  File = "File",
  Module = "Module",
  ExternalDependency = "ExternalDependency",
  Interface = "Interface",
  TypeAlias = "TypeAlias",
  Class = "Class",
  Function = "Function",
  Method = "Method",
  Constant = "Constant",
  Decorator = "Decorator",
  Route = "Route",
  Screen = "Screen",
  Component = "Component",
  APIRoute = "APIRoute",
  Layout = "Layout",
  HTTPEndpoint = "HTTPEndpoint",
  DatabaseSchema = "DatabaseSchema",
  DatabaseTable = "DatabaseTable",
  DatabaseColumn = "DatabaseColumn",
  DatabaseIndex = "DatabaseIndex",
  DatabaseEnum = "DatabaseEnum",
  ContractDocument = "ContractDocument",
  ContractEndpoint = "ContractEndpoint",
  ContractRequestSchema = "ContractRequestSchema",
  ContractResponseSchema = "ContractResponseSchema",
  DriftSignal = "DriftSignal",
  PRDSection = "PRDSection",
  ADR = "ADR",
  Ticket = "Ticket",
  GlossaryTerm = "GlossaryTerm",
  NarrativeNote = "NarrativeNote",
}

export const NODE_TYPE_VALUES = new Set<NodeType>([
  "Organization",
  "Repository",
  "Branch",
  "Commit",
  "PullRequest",
  "Directory",
  "File",
  "Module",
  "ExternalDependency",
  "Interface",
  "TypeAlias",
  "Class",
  "Function",
  "Method",
  "Constant",
  "Decorator",
  "Route",
  "Screen",
  "Component",
  "APIRoute",
  "Layout",
  "HTTPEndpoint",
  "DatabaseSchema",
  "DatabaseTable",
  "DatabaseColumn",
  "DatabaseIndex",
  "DatabaseEnum",
  "ContractDocument",
  "ContractEndpoint",
  "ContractRequestSchema",
  "ContractResponseSchema",
  "DriftSignal",
  "PRDSection",
  "ADR",
  "Ticket",
  "GlossaryTerm",
  "NarrativeNote",
]);

export function isNodeType(value: unknown): value is NodeType {
  return typeof value === "string" && NODE_TYPE_VALUES.has(value as NodeType);
}

export function assertNodeType(value: unknown): asserts value is NodeType {
  if (!isNodeType(value)) {
    throw new Error(`Invalid NodeType: ${JSON.stringify(value)}`);
  }
}

/** Extractor that populates each node type (informational). */
export const NODE_TYPE_EXTRACTOR: Record<NodeType, string> = {
  "Organization": "git",
  "Repository": "git",
  "Branch": "git",
  "Commit": "git",
  "PullRequest": "git",
  "Directory": "core-ts",
  "File": "core-ts",
  "Module": "core-ts",
  "ExternalDependency": "core-ts",
  "Interface": "core-ts",
  "TypeAlias": "core-ts",
  "Class": "core-ts",
  "Function": "core-ts",
  "Method": "core-ts",
  "Constant": "core-ts",
  "Decorator": "core-ts",
  "Route": "framework-next",
  "Screen": "framework-next",
  "Component": "framework-next",
  "APIRoute": "framework-next",
  "Layout": "framework-next",
  "HTTPEndpoint": "core-ts",
  "DatabaseSchema": "framework-prisma",
  "DatabaseTable": "framework-prisma",
  "DatabaseColumn": "framework-prisma",
  "DatabaseIndex": "framework-prisma",
  "DatabaseEnum": "framework-prisma",
  "ContractDocument": "framework-openapi",
  "ContractEndpoint": "framework-openapi",
  "ContractRequestSchema": "framework-openapi",
  "ContractResponseSchema": "framework-openapi",
  "DriftSignal": "drift-detector",
  "PRDSection": "docs-md",
  "ADR": "docs-md",
  "Ticket": "linear",
  "GlossaryTerm": "docs-md",
  "NarrativeNote": "narrative",
};

export interface OrganizationAttributes {
  /** vcs_host */
  vcs_host: string;
  /** url */
  url: string;
}

export interface RepositoryAttributes {
  /** default_branch */
  default_branch: string;
  /** vcs_url */
  vcs_url: string;
  /** primary_language */
  primary_language?: string | undefined;
  /** license */
  license?: string | undefined;
  /** languages */
  languages?: string[] | undefined;
}

export interface BranchAttributes {
  /** head_commit */
  head_commit: string;
  /** base_branch */
  base_branch?: string | undefined;
  /** is_default */
  is_default?: boolean | undefined;
}

export interface CommitAttributes {
  /** sha */
  sha: string;
  /** parent_shas */
  parent_shas: string[];
  /** author_name */
  author_name?: string | undefined;
  /** author_email */
  author_email?: string | undefined;
  /** message */
  message: string;
  /** committed_at */
  committed_at: string;
  /** tree_sha */
  tree_sha?: string | undefined;
}

export interface PullRequestAttributes {
  /** number */
  number: number;
  /** title */
  title: string;
  /** body */
  body?: string | undefined;
  /** state */
  state: string;
  /** base_branch */
  base_branch: string;
  /** head_branch */
  head_branch: string;
  /** merged_commit */
  merged_commit?: string | undefined;
  /** labels */
  labels?: string[] | undefined;
  /** draft */
  draft?: boolean | undefined;
}

export interface DirectoryAttributes {
  /** path */
  path: string;
}

export interface FileAttributes {
  /** path */
  path: string;
  /** language */
  language?: string | undefined;
  /** size_bytes */
  size_bytes?: number | undefined;
  /** content_hash */
  content_hash?: string | undefined;
  /** line_count */
  line_count?: number | undefined;
}

export interface ModuleAttributes {
  /** kind */
  kind: string;
  /** file_path */
  file_path: string;
}

export interface ExternalDependencyAttributes {
  /** package_name */
  package_name: string;
  /** registry */
  registry?: string | undefined;
  /** declared_version */
  declared_version?: string | undefined;
  /** resolved_version */
  resolved_version?: string | undefined;
  /** is_dev */
  is_dev?: boolean | undefined;
}

export interface InterfaceAttributes {
  /** generics */
  generics?: string[] | undefined;
  /** definition_text */
  definition_text?: string | undefined;
}

export interface TypeAliasAttributes {
  /** target_type */
  target_type?: string | undefined;
}

export interface ClassAttributes {
  /** generics */
  generics?: string[] | undefined;
  /** is_abstract */
  is_abstract?: boolean | undefined;
  /** modifiers */
  modifiers?: string[] | undefined;
  /** docstring */
  docstring?: string | undefined;
}

export interface FunctionAttributes {
  /** signature */
  signature?: string | undefined;
  /** is_async */
  is_async?: boolean | undefined;
  /** is_generator */
  is_generator?: boolean | undefined;
  /** line_start */
  line_start?: number | undefined;
  /** line_end */
  line_end?: number | undefined;
  /** body_hash */
  body_hash?: string | undefined;
  /** docstring */
  docstring?: string | undefined;
}

export interface MethodAttributes {
  /** signature */
  signature?: string | undefined;
  /** is_async */
  is_async?: boolean | undefined;
  /** is_static */
  is_static?: boolean | undefined;
  /** visibility */
  visibility?: string | undefined;
  /** line_start */
  line_start?: number | undefined;
  /** line_end */
  line_end?: number | undefined;
  /** body_hash */
  body_hash?: string | undefined;
}

export interface ConstantAttributes {
  /** value_text */
  value_text?: string | undefined;
  /** inferred_type */
  inferred_type?: string | undefined;
  /** exported */
  exported?: boolean | undefined;
}

export interface DecoratorAttributes {
  /** args_text */
  args_text?: string | undefined;
  /** target_kind */
  target_kind?: string | undefined;
}

export interface RouteAttributes {
  /** url_pattern */
  url_pattern: string;
  /** http_method */
  http_method?: string | undefined;
  /** dynamic_segments */
  dynamic_segments?: string[] | undefined;
}

export interface ScreenAttributes {
  /** ssr */
  ssr?: boolean | undefined;
  /** ssg */
  ssg?: boolean | undefined;
  /** permissions */
  permissions?: string[] | undefined;
}

export interface ComponentAttributes {
  /** is_server_component */
  is_server_component?: boolean | undefined;
  /** is_client_component */
  is_client_component?: boolean | undefined;
  /** exported */
  exported?: boolean | undefined;
}

export interface APIRouteAttributes {
  /** http_methods */
  http_methods: string[];
  /** path_pattern */
  path_pattern: string;
  /** dynamic_segments */
  dynamic_segments?: string[] | undefined;
  /** is_catch_all */
  is_catch_all?: boolean | undefined;
  /** router_type */
  router_type: string;
}

export interface LayoutAttributes {
  /** path_pattern */
  path_pattern: string;
  /** is_root */
  is_root?: boolean | undefined;
}

export interface HTTPEndpointAttributes {
  /** http_method */
  http_method: string;
  /** path_pattern */
  path_pattern: string;
  /** auth_required */
  auth_required?: boolean | undefined;
  /** deprecated */
  deprecated?: boolean | undefined;
}

export interface DatabaseSchemaAttributes {
  /** provider */
  provider?: string | undefined;
}

export interface DatabaseTableAttributes {
  /** schema_name */
  schema_name?: string | undefined;
}

export interface DatabaseColumnAttributes {
  /** data_type */
  data_type?: string | undefined;
  /** nullable */
  nullable?: boolean | undefined;
  /** is_primary_key */
  is_primary_key?: boolean | undefined;
  /** is_foreign_key */
  is_foreign_key?: boolean | undefined;
  /** default_value */
  default_value?: string | undefined;
}

export interface DatabaseIndexAttributes {
  /** fields */
  fields: string[];
  /** is_unique */
  is_unique?: boolean | undefined;
  /** index_type */
  index_type?: string | undefined;
}

export interface DatabaseEnumAttributes {
  /** values */
  values: string[];
}

export interface ContractDocumentAttributes {
  /** format */
  format: string;
  /** spec_version */
  spec_version?: string | undefined;
}

export interface ContractEndpointAttributes {
  /** http_method */
  http_method?: string | undefined;
  /** path */
  path?: string | undefined;
  /** operation_id */
  operation_id?: string | undefined;
  /** summary */
  summary?: string | undefined;
  /** tags */
  tags?: string[] | undefined;
  /** deprecated */
  deprecated?: boolean | undefined;
}

export interface ContractRequestSchemaAttributes {
  /** content_type */
  content_type?: string | undefined;
  /** schema_json */
  schema_json?: string | undefined;
  /** required */
  required?: boolean | undefined;
}

export interface ContractResponseSchemaAttributes {
  /** status_code */
  status_code: string;
  /** content_type */
  content_type?: string | undefined;
  /** schema_json */
  schema_json?: string | undefined;
  /** description */
  description?: string | undefined;
}

export interface DriftSignalAttributes {
  /** severity */
  severity: string;
  /** description */
  description: string;
  /** implementation_urn */
  implementation_urn: string;
  /** contract_urn */
  contract_urn: string;
  /** detected_fields */
  detected_fields?: string[] | undefined;
}

export interface PRDSectionAttributes {
  /** heading */
  heading: string;
  /** body */
  body?: string | undefined;
}

export interface ADRAttributes {
  /** status */
  status: string;
  /** decision */
  decision?: string | undefined;
  /** rationale */
  rationale?: string | undefined;
}

export interface TicketAttributes {
  /** external_id */
  external_id: string;
  /** title */
  title: string;
  /** state */
  state?: string | undefined;
  /** assignee */
  assignee?: string | undefined;
  /** url */
  url?: string | undefined;
}

export interface GlossaryTermAttributes {
  /** definition */
  definition: string;
  /** aliases */
  aliases?: string[] | undefined;
}

export interface NarrativeNoteAttributes {
  /** body */
  body: string;
  /** author_type */
  author_type: string;
  /** anchor_id */
  anchor_id: string;
}
