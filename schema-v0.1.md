# Company Brain — Graph Schema Specification

**Version:** 0.1 (draft)
**Scope:** Lossless typed graph spanning codebase + product/PRD/business artifacts
**Goal:** Every fact in the system is reconstructible from extracted graph state without loss. Summaries and embeddings are *derived views*, never the source of truth.

---

## 1. Design Principles

1. **Lossless extraction.** A node only stores facts that are either (a) directly present in the source artifact (verbatim), or (b) deterministically derivable from it (e.g., AST shape). LLM-derived summaries live in a separate `NarrativeNote` node, never overwrite extracted fields.
2. **Provenance on every fact.** Every node and edge carries `source_uri`, `extracted_from_commit`, `extractor`, `extraction_timestamp`. No anonymous facts.
3. **Temporal validity.** Nodes/edges carry `valid_from_commit` and `valid_to_commit` (or `valid_to=null` for current). The graph supports point-in-time queries.
4. **Identity stability.** IDs are content/path-derived URNs, not surrogate keys, so the same logical entity gets the same ID across re-indexes.
5. **Layered.** Structural (deterministic) → Semantic (embeddings, derived) → Narrative (human/LLM authored, anchored). All three reference the same node IDs.
6. **Source-system fidelity.** For external artifacts (Linear, Notion, Figma), store the raw payload alongside the parsed node so we can re-parse without re-fetching.
7. **Open-world.** Missing data is recorded as missing, not assumed. `null` means "we did not extract this," not "it does not exist."

---

## 2. Common Node Properties

Every node, regardless of type, carries the following envelope:

```ts
type NodeEnvelope = {
  // Identity
  id: string;                          // URN, stable across re-indexes
  type: NodeType;                      // discriminator (see catalog)
  name: string;                        // human-readable short name
  qualified_name?: string;             // fully-qualified within its scope
  aliases?: string[];                  // alternate names / former names

  // Provenance
  source_uri: string;                  // canonical link (file://, https://, urn:linear:...)
  source_range?: SourceRange;          // line/col span for code; section anchor for docs
  source_checksum: string;             // sha256 of the source slice (cache + drift)
  extractor: { name: string; version: string };
  extraction_timestamp: string;        // ISO-8601
  confidence: number;                  // 0..1 (1.0 for AST-derived, lower for LLM)
  derivation: 'ast' | 'lsp' | 'parser' | 'config' | 'llm' | 'human' | 'api';

  // Temporal validity (commit-anchored, not wall-clock)
  created_at_commit: string;
  last_modified_commit: string;
  valid_from_commit: string;
  valid_to_commit: string | null;      // null = current

  // Lifecycle
  status: 'active' | 'deprecated' | 'removed' | 'planned' | 'draft';
  deprecated_in_commit?: string;
  removed_in_commit?: string;

  // Cross-cutting
  tags?: string[];
  raw_payload?: object;                // verbatim source (for re-parse)
  attributes: Record<string, unknown>; // type-specific properties (see catalog)
};

type SourceRange = {
  start: { line: number; column: number; offset: number };
  end:   { line: number; column: number; offset: number };
};
```

---

## 3. Common Edge Properties

```ts
type EdgeEnvelope = {
  id: string;
  type: EdgeType;
  source_id: string;
  target_id: string;
  cardinality: '1-1' | '1-n' | 'n-n';

  // Provenance (same shape as nodes)
  source_uri: string;
  source_range?: SourceRange;
  extractor: { name: string; version: string };
  derivation: 'ast' | 'lsp' | 'static' | 'dynamic' | 'config' | 'llm' | 'human';
  confidence: number;

  // Temporal
  valid_from_commit: string;
  valid_to_commit: string | null;

  // Edge-specific properties
  attributes: Record<string, unknown>;
};
```

---

## 4. Node Catalog — Codebase

### 4.1 VCS & Project Structure

| # | Node | Lossless attributes |
|---|------|--------------------|
| 1 | `Organization` | name, vcs_host, url |
| 2 | `Repository` | name, default_branch, vcs_url, license, primary_language, languages[] |
| 3 | `Branch` | name, head_commit, base_branch, protection_rules |
| 4 | `Commit` | sha, parent_shas[], author, committer, message, signed, tree_sha |
| 5 | `Tag` | name, target_commit, type (lightweight/annotated), message |
| 6 | `Release` | tag, name, body, published_at, assets[], prerelease |
| 7 | `PullRequest` | number, title, body, state, base_branch, head_branch, merged_commit, labels[], reviewers[], merge_strategy |
| 8 | `Review` | reviewer, state (approved/changes_requested/commented), body, submitted_at |
| 9 | `ReviewComment` | path, line, body, in_reply_to, resolved |
| 10 | `Discussion` | title, body, category, state, locked |
| 11 | `Identity` | name, email, vcs_logins[], person_id (link to Person) |

### 4.2 File System

| # | Node | Lossless attributes |
|---|------|--------------------|
| 12 | `Directory` | path, mode |
| 13 | `File` | path, size, content_hash, encoding, mode, language, eol, mime_type |
| 14 | `Symlink` | path, target_path |
| 15 | `Submodule` | path, target_url, pinned_sha |

### 4.3 Module System

| # | Node | Lossless attributes |
|---|------|--------------------|
| 16 | `Workspace` | path, manifest_file, package_manager (pnpm/yarn/turborepo/nx) |
| 17 | `Package` | name, version, manifest_path, registry, scope, public, exports_map |
| 18 | `Module` | qualified_name, file_id, language, kind (esm/cjs/typescript_namespace/python_module/go_package/...) |
| 19 | `Namespace` | qualified_name, parent_namespace |
| 20 | `Import` | importing_file_id, source_specifier (e.g., `react`, `./utils`), imported_symbols[], default_import?, namespace_import?, type_only, dynamic, resolved_target_id |
| 21 | `Export` | declaring_file_id, exported_name, local_name, kind (named/default/re-export), type_only, resolved_target_id |
| 22 | `ExternalDependency` | package_name, registry, declared_version_range, resolved_version, integrity_hash, dev/peer/optional |
| 23 | `VersionConstraint` | spec, semver_range, source_manifest |

### 4.4 Type System (language-agnostic)

| # | Node | Lossless attributes |
|---|------|--------------------|
| 24 | `TypeDeclaration` | name, kind, definition_text, generics[] |
| 25 | `PrimitiveType` | name (string/int/bool/...), language |
| 26 | `LiteralType` | value, base_type |
| 27 | `UnionType` | members[] |
| 28 | `IntersectionType` | members[] |
| 29 | `TupleType` | members[], rest_type? |
| 30 | `FunctionType` | params[], return_type, variadic |
| 31 | `GenericParameter` | name, constraint?, default? |
| 32 | `TypeAlias` | name, target_type, generics[] |
| 33 | `Interface` | name, generics[], extends[], members[] |
| 34 | `EnumType` | name, members[], underlying_type |
| 35 | `EnumMember` | name, value |
| 36 | `Schema` (JSON Schema, Zod, Pydantic, etc.) | name, format, raw, fields[] |

### 4.5 Code Structure

| # | Node | Lossless attributes |
|---|------|--------------------|
| 37 | `Class` | name, qualified_name, generics[], is_abstract, is_final, modifiers[], docstring |
| 38 | `Trait` / `Mixin` | name, generics[], members[] |
| 39 | `Function` | name, qualified_name, signature, generics[], is_async, is_generator, is_pure, modifiers[], docstring, body_hash |
| 40 | `Method` | (Function fields) + owner_class_id, is_static, is_virtual, is_override, visibility |
| 41 | `Constructor` | owner_class_id, params[] |
| 42 | `Property` / `Field` | name, type, owner_id, is_static, is_readonly, default_value_text, visibility |
| 43 | `Parameter` | name, type, default_value_text, position, is_rest, is_optional, decorators[] |
| 44 | `LocalVariable` | name, type, scope_id, is_const, initializer_text |
| 45 | `Constant` | name, type, value_text, exported |
| 46 | `Decorator` / `Annotation` | name, args_text, target_kind |
| 47 | `Comment` | kind (line/block/doc), text, attached_to_id?, todo_marker? |
| 48 | `Macro` | name, args_text, expansion_hint |
| 49 | `CodeBlock` | id, kind (try/catch/with/loop/...), parent_id |

### 4.6 Frontend (UI)

| # | Node | Lossless attributes |
|---|------|--------------------|
| 50 | `Application` | name, framework (next/react/vue/svelte/angular/...), entry_file, version |
| 51 | `Route` | url_pattern, method?, file_id, dynamic_segments[], catch_all, layout_chain[] |
| 52 | `Screen` / `Page` | name, route_id, ssr/ssg/csr, metadata (title/description), permissions[] |
| 53 | `Layout` | name, slot_definition, file_id |
| 54 | `Component` | name, kind (function/class), file_id, is_server_component?, is_client_component?, exported, generics[] |
| 55 | `ComponentProp` | name, type, required, default_value_text, owner_component_id, controlled? |
| 56 | `ComponentSlot` | name, owner_component_id, accepts |
| 57 | `Hook` | name, returns, parameters[], rules_violations[]? |
| 58 | `CustomHook` | (Hook) + dependencies[] |
| 59 | `StoreSlice` / `Atom` | name, library (redux/zustand/jotai/recoil/pinia), state_shape, file_id |
| 60 | `Action` | type, payload_schema, slice_id |
| 61 | `Reducer` | slice_id, handles_actions[] |
| 62 | `Selector` | name, dependencies[], output_type |
| 63 | `Effect` / `Saga` / `Thunk` | name, triggers[], side_effects[] |
| 64 | `StyleRule` | selector, properties, file_id, scope (global/module/scoped) |
| 65 | `ThemeToken` | name, value, category (color/spacing/typography/...), source_system (figma/css-vars) |
| 66 | `Asset` | path, kind (image/font/svg/video), checksum, dimensions?, used_by[] |
| 67 | `TranslationKey` | key, namespace, locales: { [locale]: text }, plural_forms? |
| 68 | `A11yAnnotation` | role, aria_attrs, attached_to_id, axe_rule_overrides? |
| 69 | `Form` | name, fields[], submit_handler_id, validation_schema_id |
| 70 | `FormField` | name, kind, validation_rules[], default_value, owner_form_id |
| 71 | `ValidationRule` | kind (required/regex/min/max/custom), config, error_message_key |
| 72 | `AnalyticsEvent` | name, properties[], emitted_from[], destination (segment/amplitude/posthog/...) |
| 73 | `KeyboardShortcut` | combo, scope, action_id |
| 74 | `Modal` / `Drawer` / `Dialog` | name, trigger_ids[], content_component_id |

### 4.7 Backend / API Implementation

| # | Node | Lossless attributes |
|---|------|--------------------|
| 75 | `HTTPEndpoint` | method, path, handler_id, route_params[], query_params[], headers[], status_codes[], content_types_in[], content_types_out[], file_id |
| 76 | `RouteParam` | name, type, position, validation, optional |
| 77 | `Handler` | function_id, framework, request_type, response_type |
| 78 | `Controller` | class_id, base_path, mounted_endpoints[] |
| 79 | `Middleware` | name, applied_to[], order, side_effects[] |
| 80 | `AuthGuard` | name, required_permissions[], required_roles[], applied_to[] |
| 81 | `RateLimit` | window, limit, key_fn, applied_to[] |
| 82 | `Service` (business logic class) | name, owns_use_cases[], dependencies[] |
| 83 | `Repository` (DAO) | name, entity_id, supported_ops[] |
| 84 | `UseCase` / `Interactor` | name, input_schema, output_schema, invariants[] |
| 85 | `EventHandler` | event_type, handler_id, idempotency_key_fn, retry_policy |
| 86 | `JobDefinition` | name, queue, handler_id, schedule?, retry_policy, timeout_ms |
| 87 | `CronSchedule` | expression, timezone, job_id |
| 88 | `WorkerPool` | name, concurrency, queue_subscriptions[] |
| 89 | `QueueTopic` | name, broker (sqs/sns/kafka/rabbit/redis), schema_id, partition_key, dlq |
| 90 | `Event` | name, schema_id, version, producers[], consumers[] |
| 91 | `WebSocketChannel` | path, message_schemas[], auth_required |
| 92 | `WebSocketEvent` | name, direction (in/out), schema_id |
| 93 | `SSEStream` | path, event_types[], heartbeat_interval |
| 94 | `GraphQLSchema` | sdl_text, version |
| 95 | `GraphQLType` | name, kind (Object/Input/Interface/Union/Enum/Scalar), fields[], directives[] |
| 96 | `GraphQLField` | name, args[], return_type, directives[], resolver_id |
| 97 | `GraphQLResolver` | type_id, field_id, function_id, dataloader? |
| 98 | `GraphQLDirective` | name, args[], applies_to[] |
| 99 | `GraphQLSubscription` | name, return_type, transport |
| 100 | `gRPCService` | name, package, methods[], proto_file_id |
| 101 | `gRPCMethod` | name, input_message_id, output_message_id, streaming (none/client/server/bidi) |
| 102 | `ProtoMessage` | name, fields[], reserved[], oneofs[] |
| 103 | `BatchEndpoint` / `BulkOp` | base_endpoint_id, max_batch_size, transactional |

### 4.8 API Contracts (separate from implementation — the source of truth for "what the API promises")

| # | Node | Lossless attributes |
|---|------|--------------------|
| 104 | `ContractDocument` | name, kind (openapi/asyncapi/graphql/grpc/postman/raml), version, document_hash, raw |
| 105 | `ContractEndpoint` | method, path, summary, parameters[], request_body_schema_id, response_schemas[], deprecated, security[] |
| 106 | `ContractRequestSchema` | content_type, schema_id, examples[] |
| 107 | `ContractResponseSchema` | status_code, content_type, schema_id, headers[], examples[] |
| 108 | `ContractErrorSchema` | status_code, error_code, schema_id |
| 109 | `ContractExample` | name, value, language |
| 110 | `ContractVersion` | version_string, released_at, changelog_id, breaking |
| 111 | `WebhookContract` | event, payload_schema_id, signing_method, retry_policy, idempotency_header |
| 112 | `WebhookEvent` | name, version, schema_id |
| 113 | `BackwardsCompatibilityRule` | from_version, to_version, kind (additive/breaking), rationale |

### 4.9 Data Layer

| # | Node | Lossless attributes |
|---|------|--------------------|
| 114 | `Database` | name, engine (postgres/mysql/dynamo/...), version, connection_alias |
| 115 | `DatabaseSchema` | name, owner_db_id |
| 116 | `Table` | name, schema_id, columns[], pk_id, comment, partitioning, ttl |
| 117 | `Column` | name, type, nullable, default_value, generated, comment, length, precision |
| 118 | `PrimaryKey` | columns[], name |
| 119 | `ForeignKey` | columns[], references_table_id, references_columns[], on_delete, on_update |
| 120 | `UniqueConstraint` | columns[], name, where_clause? |
| 121 | `CheckConstraint` | name, expression |
| 122 | `Index` | name, columns[], unique, type (btree/gin/hash/...), where_clause? |
| 123 | `Trigger` | name, table_id, timing, events[], function_body |
| 124 | `View` | name, definition_sql, materialized |
| 125 | `MaterializedView` | name, refresh_policy, definition_sql |
| 126 | `StoredProcedure` / `Function` | name, params[], return_type, body |
| 127 | `Sequence` | name, start, increment, owner_column? |
| 128 | `Migration` | name, up_sql, down_sql, ordering, applied_in_release? |
| 129 | `ORMEntity` | name, table_id, file_id, decorators[] |
| 130 | `ORMField` | name, column_id, type, transformers[], is_relation, relation_kind (1-1/1-n/n-n) |
| 131 | `ORMRelation` | from_entity_id, to_entity_id, kind, join_table_id?, eager? |
| 132 | `NamedQuery` | name, sql_text, params[], expected_shape |
| 133 | `SeedData` | table_id, rows_hash, source_file_id |
| 134 | `DataModel` (logical) | name, fields[], invariants[] (used to bridge ORM ↔ business `DomainConcept`) |

### 4.10 Configuration

| # | Node | Lossless attributes |
|---|------|--------------------|
| 135 | `ConfigFile` | path, format (json/yaml/toml/env/...), schema_id? |
| 136 | `ConfigKey` | path (dot-notation), type, default_value, required, applies_to_envs[] |
| 137 | `EnvironmentVariable` | name, used_in[], required, default, secret_flag, schema_id? |
| 138 | `FeatureFlag` | key, kind (boolean/multivariate/percentage), provider (launchdarkly/unleash/posthog/custom), default, owner |
| 139 | `FeatureFlagVariant` | flag_id, key, value, rollout_rules[] |
| 140 | `SecretReference` | name, source (vault/aws-sm/env), used_in[]; never the value itself |
| 141 | `BuildTarget` | name, kind (app/lib/test), inputs[], outputs[], runner |
| 142 | `BuildConfig` | name, file_id, scripts |
| 143 | `DeploymentConfig` | name, environment, image_tag, replicas, resource_limits, env_overrides |
| 144 | `RuntimeProfile` | name (dev/staging/prod), region, traffic_percent |

### 4.11 Infrastructure

| # | Node | Lossless attributes |
|---|------|--------------------|
| 145 | `ServiceDefinition` | name, kind (app/job/worker/cron), language, image, ports, health_check |
| 146 | `Container` | name, image, command, args, env_keys[], volumes[] |
| 147 | `K8sResource` | kind, apiVersion, metadata, spec_hash |
| 148 | `TerraformResource` | type, name, provider, config, state_id_ref |
| 149 | `CloudResource` | provider, kind, region, identifier |
| 150 | `CDNRule` | host, path_pattern, origin, cache_policy |
| 151 | `CacheLayer` | name, kind (redis/memcached/inmem), ttl_default, eviction |
| 152 | `LoadBalancer` | name, listeners[], target_groups[] |
| 153 | `DNSRecord` | name, type, value, ttl |
| 154 | `Certificate` | subject, issuer, expires_at, used_by[] |
| 155 | `NetworkPolicy` | name, ingress_rules[], egress_rules[] |
| 156 | `IAMPolicy` | name, statements[], attached_to[] |

### 4.12 Testing

| # | Node | Lossless attributes |
|---|------|--------------------|
| 157 | `TestSuite` | name, file_id, framework, tags[] |
| 158 | `TestCase` | name, suite_id, kind (unit/integration/e2e/contract/property), tags[], skip_reason? |
| 159 | `TestAssertion` | case_id, kind (equals/throws/matches/snapshot/...), subject_text, expected_text, source_range |
| 160 | `Mock` / `Stub` | replaces_id, kind (function/module/network), behavior |
| 161 | `Fixture` | name, scope, value_hash, kind (data/state/network) |
| 162 | `Snapshot` | name, path, content_hash, last_updated_commit |
| 163 | `CoverageReport` | commit, scope, lines_covered, branches_covered, file_breakdown_id |
| 164 | `PerformanceBenchmark` | name, scenario, metric, baseline, threshold |
| 165 | `ContractTest` | contract_id, endpoint_id, scenarios[] |

### 4.13 Observability

| # | Node | Lossless attributes |
|---|------|--------------------|
| 166 | `LogStatement` | level, message_template, attributes_extracted[], source_range |
| 167 | `MetricDefinition` | name, kind (counter/gauge/histogram/summary), unit, labels[], description |
| 168 | `TraceSpan` (definition) | name, attributes[], emitted_at_id (function/middleware) |
| 169 | `Dashboard` | name, panels[], query_refs[], owner |
| 170 | `Alert` | name, condition_id, severity, channels[], runbook_id |
| 171 | `AlertCondition` | metric_id, threshold, window, evaluation |
| 172 | `Runbook` | name, steps[], owner, attached_to[] |
| 173 | `SLI` | name, query, unit |
| 174 | `SLO` | name, sli_id, objective, window |
| 175 | `ErrorClass` (catalog) | code, message_template, http_status, surface |

### 4.14 Security & Auth

| # | Node | Lossless attributes |
|---|------|--------------------|
| 176 | `Permission` | key, description, resource_kind |
| 177 | `Role` | key, granted_permissions[], inherits[] |
| 178 | `AuthScope` | name, granted_permissions[], applies_to[] (oauth scopes / api keys) |
| 179 | `AuthPolicy` | name, body, applies_to[] |
| 180 | `AuthenticationMethod` | name, kind (password/oauth/saml/oidc/api-key/jwt), provider, config |
| 181 | `EncryptionPolicy` | scope, algorithm, key_source |
| 182 | `Vulnerability` | cve, package, severity, fixed_in, status |
| 183 | `SBOMEntry` | package, version, license, source |

---

## 5. Node Catalog — Product / PRD / Business

### 5.1 Product Hierarchy

| # | Node | Lossless attributes |
|---|------|--------------------|
| 184 | `Company` | name, mission, public_url |
| 185 | `Product` | name, description, owner_team_id, vision_doc_id |
| 186 | `ProductLine` / `Pillar` | name, parent_product_id |
| 187 | `Feature` | name, status, target_release, description, owner_team_id |
| 188 | `Epic` | name, summary, parent_feature_id, status |
| 189 | `UserStory` | as_a, i_want, so_that, gherkin_text?, story_points |
| 190 | `Requirement` | id, statement, kind (functional/non-functional/constraint), priority (MoSCoW) |
| 191 | `AcceptanceCriterion` | given, when, then, owner_story_id |
| 192 | `UseCase` | name, primary_actor, preconditions, main_flow, alt_flows, postconditions |
| 193 | `UserPersona` | name, demographics, goals, pain_points, scenarios[] |
| 194 | `UserJourney` | name, persona_id, steps[], pain_points[], opportunities[] |
| 195 | `JobToBeDone` | when, i_want, so_i_can, forces, alternatives |
| 196 | `Workflow` | name, steps[] (each step → screen/endpoint), entry_points[], exit_points[] |
| 197 | `Capability` | name, description, supports_features[] |

### 5.2 Specs & Decisions

| # | Node | Lossless attributes |
|---|------|--------------------|
| 198 | `PRD` | title, version, status, author, raw_document_id, sections[] |
| 199 | `PRDSection` | heading, anchor, body_text, body_hash, ordering, parent_section_id |
| 200 | `DesignSpec` | title, figma_url, version, owner |
| 201 | `TechnicalSpec` | title, body, status, author |
| 202 | `ADR` | number, title, status (proposed/accepted/superseded), context, decision, consequences, alternatives_considered[] |
| 203 | `RFC` | number, title, body, status, comments_count |
| 204 | `Wireframe` | name, source_url, version |
| 205 | `Mockup` | name, figma_node_id, image_url, design_spec_id |
| 206 | `Prototype` | name, source_url, scenarios_demonstrated[] |
| 207 | `DesignToken` | name, value, type (color/spacing/typography/radius/shadow), source (figma/css) |
| 208 | `DesignSystem` | name, tokens[], components_documented[] |
| 209 | `FigmaFrame` | id, name, file_key, page, components[] |
| 210 | `UserResearchFinding` | summary, source_study_id, evidence[], confidence |
| 211 | `Persona` (research) | name, attributes, source_studies[] |
| 212 | `CompetitorAnalysis` | competitor, feature, our_position, source |
| 213 | `MarketRequirement` | source, statement, priority |

### 5.3 Work Tracking

| # | Node | Lossless attributes |
|---|------|--------------------|
| 214 | `Ticket` | external_id (LIN-123/JIRA-456), title, body, state, priority, assignee, reporter, labels[], parent_id, source_system |
| 215 | `Subtask` | (Ticket) + parent_ticket_id |
| 216 | `Sprint` | name, start, end, team_id, planned_capacity, completed |
| 217 | `Milestone` | name, due, scope_tickets[], state |
| 218 | `Roadmap` | name, horizon, owner |
| 219 | `RoadmapItem` | roadmap_id, feature_id, quarter, confidence |
| 220 | `ReleaseNote` | release_id, audience (internal/external), body, items[] |
| 221 | `ChangelogEntry` | version, kind (added/changed/fixed/removed/security), body |
| 222 | `Estimate` | ticket_id, kind (story_points/hours), value, by_person, at_time |

### 5.4 Decisions, Constraints, Risks

| # | Node | Lossless attributes |
|---|------|--------------------|
| 223 | `Decision` | summary, context, options_considered[], chosen, rationale, decided_by, decided_at |
| 224 | `Tradeoff` | dimension (perf/cost/dx/...), preferred, sacrificed, rationale |
| 225 | `Constraint` | kind (technical/business/legal/budget/timeline), statement, source |
| 226 | `NonGoal` | statement, scope_id, rationale |
| 227 | `OpenQuestion` | question, owner, due_by, status |
| 228 | `Risk` | description, likelihood, impact, mitigation_id?, owner |
| 229 | `Mitigation` | description, owner, status |
| 230 | `Assumption` (business-level) | statement, validated, evidence_ids[] |

### 5.5 People & Org

| # | Node | Lossless attributes |
|---|------|--------------------|
| 231 | `Team` | name, parent_team_id, members[], charter |
| 232 | `Person` | name, email, identities[] (vcs/slack/linear), roles[], manager_id |
| 233 | `RoleAssignment` | person_id, role_kind (PM/EM/IC/Designer/QA/...), scope_id, since |
| 234 | `ResponsibilityArea` (DRI) | area_name, owner_id, escalation_id |
| 235 | `StakeholderGroup` | name, members[], interest_in[] |
| 236 | `Reviewer` | person_id, scope (path glob / team / module), required |
| 237 | `OncallRotation` | team_id, schedule, current_oncall_id |

### 5.6 Knowledge & Domain Model

| # | Node | Lossless attributes |
|---|------|--------------------|
| 238 | `GlossaryTerm` | term, definition, synonyms[], domain |
| 239 | `DomainConcept` | name, definition, attributes[], invariants[] |
| 240 | `BusinessRule` | id, statement, formal_expression?, owner, source_doc_id |
| 241 | `Invariant` (business or system) | statement, formal_expression?, scope_id, enforcement (db_constraint/test/runtime/manual) |
| 242 | `DataAssumption` | statement (e.g., "user_id is UUID v4"), evidence_ids[], scope, last_validated_commit |
| 243 | `Hypothesis` | statement, predicted_outcome, success_metric, status |
| 244 | `Experiment` | name, hypothesis_id, variants[], population, start, end, result |
| 245 | `ExperimentVariant` | name, allocation_percent, feature_flag_id |
| 246 | `BusinessMetric` | name, definition_sql_or_formula, owner, dashboards[] |
| 247 | `KPI` | name, target, current, business_metric_id, owner |
| 248 | `OKR` | objective, key_results[], owner, period |
| 249 | `KeyResult` | description, target, current, parent_okr_id |
| 250 | `Pattern` (architectural pattern in use) | name, description, exemplar_ids[] |

### 5.7 Customer-Facing Artifacts

| # | Node | Lossless attributes |
|---|------|--------------------|
| 251 | `HelpArticle` | title, slug, body, audience, last_reviewed |
| 252 | `FAQEntry` | question, answer, related_artifact_ids[] |
| 253 | `APIDocPage` | path, contract_endpoint_id, examples[] |
| 254 | `Tutorial` | title, steps[], prerequisites[] |
| 255 | `CodeExample` | language, code, context, demonstrates_id (component/endpoint) |
| 256 | `SampleApp` | name, repo_id, demonstrates[] |
| 257 | `BlogPost` | title, body, author, published_at, related_features[] |
| 258 | `PublicChangelog` | release_id, audience, summary |

### 5.8 Compliance & Legal

| # | Node | Lossless attributes |
|---|------|--------------------|
| 259 | `ComplianceRequirement` | framework (GDPR/SOC2/HIPAA/PCI-DSS/...), control_id, statement |
| 260 | `DataClassification` | name (public/internal/confidential/restricted/PII/PHI/PCI), criteria |
| 261 | `PIIField` | column_id, classification_id, masking_strategy, retention_days |
| 262 | `Policy` | name, body, scope, owner, effective_from |
| 263 | `RetentionPolicy` | data_class_id, max_age_days, deletion_strategy |
| 264 | `ConsentDefinition` | name, purpose, lawful_basis, evidence_required |
| 265 | `AuditEventDefinition` | name, fields_logged[], emitted_from[], retention_days |
| 266 | `DataResidencyRule` | data_class_id, allowed_regions[] |

### 5.9 Quality & Incidents

| # | Node | Lossless attributes |
|---|------|--------------------|
| 267 | `Incident` | id, title, severity, started_at, resolved_at, status, services_affected[], commander |
| 268 | `Postmortem` | incident_id, summary, timeline[], root_causes[], action_items[] |
| 269 | `BugReport` | title, repro_steps, expected, actual, environment, severity, ticket_id |
| 270 | `KnownIssue` | title, workaround_id?, affected_versions[], status |
| 271 | `Workaround` | description, applies_to_id, side_effects |
| 272 | `DeprecationPlan` | target_id, deprecated_in, removed_in, migration_guide_id |
| 273 | `RegressionTest` | bug_id, test_case_id |
| 274 | `ActionItem` | description, owner, due, status, source_postmortem_id? |

### 5.10 External / Third-party

| # | Node | Lossless attributes |
|---|------|--------------------|
| 275 | `ThirdPartyService` | name, vendor, category, used_in[], criticality |
| 276 | `VendorContract` | vendor, term, renewal_date, owner, document_url |
| 277 | `SLAAgreement` | service_id, metric, target, penalty |
| 278 | `IntegrationPartner` | name, contract_id, contact, supported_endpoints[] |
| 279 | `ExternalAPI` | base_url, auth_method, rate_limits, documented_at_url |

### 5.11 Narrative, Provenance & Drift

| # | Node | Lossless attributes |
|---|------|--------------------|
| 280 | `NarrativeNote` | body, anchored_to[] (node ids), authored_by (person/llm), confidence, supersedes_id? |
| 281 | `ExtractedFact` | statement, subject_id, predicate, object, evidence_ids[], confidence |
| 282 | `Annotation` | target_id, kind (todo/fixme/security/perf/risk), body |
| 283 | `Citation` | source_node_id, used_in_response_id, snippet |
| 284 | `ChangeEvent` | commit, kind (added/modified/deleted/renamed), node_id, before_hash, after_hash, semantic_diff |
| 285 | `DriftSignal` | anchor_id (narrative), code_node_id, drift_kind (signature/behavior/deletion), severity, detected_in_commit |
| 286 | `Skill` (procedural knowledge bundle) | name, when_to_use, steps[], referenced_node_ids[], version |
| 287 | `Snapshot` (graph-level) | name, commit, included_node_types[], stats |

---

## 6. Edge Catalog

Edges are organized by relationship family. Source/Target columns list the most important valid pairs (extend conservatively). Every edge carries the envelope from §3.

### 6.1 Containment

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `contains` | Repository → File/Directory; Directory → File/Directory; File → Module; Module → Class/Function/...; Class → Method/Field; PRD → PRDSection; ContractDocument → ContractEndpoint | Inverse: `belongs_to` |
| `declared_in` | Class/Function/Type → File | Source location |
| `defined_in` | TypeAlias/Interface → Module | Logical scope |
| `member_of` | Method → Class; EnumMember → EnumType | |
| `scoped_to` | LocalVariable → CodeBlock | |

### 6.2 Code Structural (deterministic, AST/LSP-derived)

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `imports` | File/Module → Module/Package; props: { specifier, type_only, dynamic } | |
| `exports` | Module → Symbol; props: { kind: named/default/re-export } | |
| `re_exports` | Module → Module | |
| `extends` | Class → Class; Interface → Interface | |
| `implements` | Class → Interface | |
| `mixes_in` | Class → Trait | |
| `composes` | Class → Class (via field) | Derived |
| `references` | Function/Method → Symbol; props: { is_call, is_type_ref, is_value_ref } | |
| `calls` | Function → Function; props: { is_async_await, in_loop, conditional } | |
| `instantiates` | Function → Class | |
| `accesses` | Function → Property/Field | |
| `mutates` | Function → Property/Field/Variable | |
| `throws` | Function → ErrorClass | |
| `catches` | Function → ErrorClass | |
| `decorated_by` | Function/Class → Decorator | |
| `annotated_with` | * → Annotation | |

### 6.3 Type Relations

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `has_type` | Field/Param/Variable → Type | |
| `returns` | Function → Type | |
| `accepts` | Function → Type (param positional) | |
| `parameterizes` | Generic → TypeParameter | |
| `narrows_to` | Union → Type | flow narrowing |
| `is_subtype_of` | Type → Type | |
| `assignable_to` | Type → Type | derived |
| `validates_with` | Function/Field → Schema | Zod/Yup/Pydantic |

### 6.4 Data Flow

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `reads_from` | Function → Table/Column/Cache/Config | |
| `writes_to` | Function → Table/Column/Cache | |
| `subscribes_to` | EventHandler/Hook → Event/Topic/Channel/Store | |
| `emits` | Function → Event/AnalyticsEvent/LogStatement | |
| `publishes` | Function → QueueTopic/Channel | |
| `consumes` | WorkerPool → QueueTopic | |
| `produces` | Endpoint → ContractResponseSchema | |
| `accepts_payload` | Endpoint → ContractRequestSchema | |

### 6.5 Frontend-Specific

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `renders` | Component → Component | child usage |
| `mounts_in` | Screen → Layout | |
| `routes_to` | Route → Screen | |
| `navigates_to` | Component/Hook → Route; props: { trigger } | |
| `props_to` | Component → ComponentProp; ComponentProp ← passed-from Component | |
| `uses_hook` | Component/Hook → Hook | |
| `subscribes_to_state` | Component/Hook → StoreSlice/Selector | |
| `dispatches` | Component/Hook → Action | |
| `styled_by` | Component → StyleRule/ThemeToken | |
| `references_asset` | Component → Asset | |
| `uses_translation` | Component → TranslationKey | |
| `binds_form` | Component → Form | |
| `validated_by` | FormField → ValidationRule | |
| `tracks_event` | Component/Hook → AnalyticsEvent | |

### 6.6 Backend / API

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `handles` | Handler/Function → HTTPEndpoint | |
| `mounted_under` | HTTPEndpoint → Controller | |
| `applies_middleware` | HTTPEndpoint → Middleware; props: { order } | |
| `guarded_by` | HTTPEndpoint → AuthGuard | |
| `rate_limited_by` | HTTPEndpoint → RateLimit | |
| `implements_contract` | HTTPEndpoint → ContractEndpoint | KEY edge for drift detection |
| `validates_against` | Handler → Schema | |
| `responds_with` | HTTPEndpoint → ContractResponseSchema | |
| `errors_with` | HTTPEndpoint → ErrorClass / ContractErrorSchema | |
| `resolved_by` | GraphQLField → GraphQLResolver | |
| `triggers_job` | Function → JobDefinition | |
| `scheduled_by` | JobDefinition → CronSchedule | |
| `subscribes_event` | EventHandler → Event | |
| `emits_event` | Function → Event | |

### 6.7 Data / DB

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `maps_to_table` | ORMEntity → Table | |
| `maps_to_column` | ORMField → Column | |
| `has_column` | Table → Column | |
| `pk_of` | PrimaryKey → Table | |
| `fk_to` | Column → Column; props: { on_delete, on_update } | |
| `indexed_by` | Table → Index; Column → Index | |
| `constrained_by` | Table → CheckConstraint/UniqueConstraint | |
| `migrated_by` | Table/Column → Migration; props: { kind: created/altered/dropped } | |
| `derived_from` | View → Table; MaterializedView → Table | |
| `seeds` | SeedData → Table | |
| `models_concept` | ORMEntity/Table → DomainConcept | bridge to business |

### 6.8 Test

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `tests` | TestCase → Function/Endpoint/Component/Contract | |
| `asserts` | TestCase → TestAssertion | |
| `mocks` | TestCase → Function/Module/ExternalAPI | |
| `covers` | CoverageReport → File/Function; props: { line_pct, branch_pct } | |
| `verifies` | ContractTest → ContractEndpoint | |
| `regresses` | RegressionTest → BugReport | |
| `pins_snapshot` | TestCase → Snapshot | |
| `benchmarks` | PerformanceBenchmark → Function/Endpoint | |

### 6.9 Configuration & Infra

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `reads_config` | Function → ConfigKey/EnvironmentVariable/FeatureFlag | |
| `gated_by` | Function/Endpoint/Component → FeatureFlag | |
| `uses_secret` | Function → SecretReference | |
| `deploys_as` | Service → DeploymentConfig | |
| `runs_in` | Service → RuntimeProfile | |
| `provisioned_by` | CloudResource → TerraformResource/K8sResource | |
| `routed_by` | Service → LoadBalancer/CDNRule | |
| `cached_in` | Endpoint/Function → CacheLayer; props: { ttl, key_pattern } | |
| `network_governed_by` | Service → NetworkPolicy | |
| `grants` | IAMPolicy → Permission | |

### 6.10 Observability

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `instrumented_by` | Function/Endpoint → MetricDefinition/TraceSpan/LogStatement | |
| `monitored_by` | Service/Endpoint → Dashboard | |
| `alerted_by` | Service/Endpoint → Alert | |
| `evaluated_by` | Alert → AlertCondition | |
| `runbook_for` | Runbook → Alert/Service | |
| `measures` | SLI → Endpoint/Service | |
| `objective_for` | SLO → SLI | |

### 6.11 Security & Compliance

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `requires_permission` | Endpoint/Function → Permission | |
| `requires_role` | Endpoint → Role | |
| `authenticated_by` | Endpoint → AuthenticationMethod | |
| `handles_pii` | Function/Endpoint/Column → PIIField/DataClassification | |
| `subject_to_policy` | * → Policy/RetentionPolicy/ComplianceRequirement | |
| `audited_by` | Function → AuditEventDefinition | |
| `vulnerable_to` | ExternalDependency → Vulnerability | |
| `encrypted_by` | Column/Field → EncryptionPolicy | |

### 6.12 Lifecycle & Versioning (temporal edges)

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `created_in_commit` | Node → Commit | |
| `modified_in_commit` | Node → Commit; props: { kind: signature/body/move/rename } | |
| `deleted_in_commit` | Node → Commit | |
| `introduced_in_pr` | Node → PullRequest | |
| `released_in` | Node → Release | |
| `deprecated_by` | Node → DeprecationPlan/Decision | |
| `replaced_by` | Node → Node | |
| `supersedes` | ADR/Decision → ADR/Decision | |
| `migrated_to` | Endpoint/Schema → Endpoint/Schema | API version evolution |
| `renamed_from` | Node → former_qualified_name (string) | |

### 6.13 Business Linkage (the "why" edges — the moat)

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `implements_requirement` | Endpoint/Component/Function/Class → Requirement | |
| `implements_story` | * → UserStory | |
| `implements_acceptance_criterion` | TestCase/Endpoint → AcceptanceCriterion | |
| `realizes_use_case` | Workflow/Screen → UseCase | |
| `delivers_feature` | * → Feature | |
| `belongs_to_epic` | Feature/Story → Epic | |
| `documented_in` | Node → PRDSection/ADR/HelpArticle/APIDocPage | |
| `decided_in` | Node → ADR/Decision | |
| `requested_by` | Feature/Ticket → Person/StakeholderGroup | |
| `approved_by` | Decision → Person | |
| `assigned_to` | Ticket → Person | |
| `owned_by` | * → Team/Person/ResponsibilityArea | |
| `reviewed_by` | PullRequest → Person | |
| `realizes_journey_step` | Screen/Endpoint → UserJourney; props: { step_index } | |

### 6.14 Dependencies / Workflow

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `depends_on` | Ticket → Ticket; Service → Service; Migration → Migration | |
| `blocks` | Ticket → Ticket | inverse of depends_on |
| `precedes` | RoadmapItem → RoadmapItem | sequencing |
| `part_of_release` | Ticket/PR → Release | |
| `tracked_by` | PR/Commit → Ticket | |

### 6.15 Quality & Incidents

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `caused_incident` | Commit/PR/Service → Incident | |
| `mitigated_by` | Incident → Workaround/PR | |
| `produced_postmortem` | Incident → Postmortem | |
| `action_item_from` | ActionItem → Postmortem/Decision | |
| `regression_in` | BugReport → Function/Endpoint/Component | |

### 6.16 Knowledge / Narrative

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `defines_term` | GlossaryTerm → DomainConcept | |
| `references_term` | * → GlossaryTerm | |
| `governs` | BusinessRule/Invariant → Function/Endpoint/Column | |
| `assumes` | Function/Component/Endpoint → DataAssumption | |
| `violates` | Code → Invariant | drift signal |
| `annotates` | NarrativeNote → Node | |
| `explains` | NarrativeNote → Node | |
| `cites` | NarrativeNote → Node | |
| `contradicts` | NarrativeNote → NarrativeNote | |
| `supersedes` | NarrativeNote → NarrativeNote | |
| `extracted_from` | ExtractedFact → File/Document | |

### 6.17 Provenance & Drift

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `derived_by` | Node → Extractor (logical) | who/what produced it |
| `verified_at_commit` | NarrativeNote → Commit; props: { still_valid } | |
| `drifts_from` | DriftSignal → NarrativeNote/Anchor | |
| `evidence_for` | Node → Hypothesis/Decision/Assumption | |

### 6.18 External Integration

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `integrates_with` | Function/Service → ThirdPartyService/ExternalAPI | |
| `governed_by_sla` | Service/Endpoint → SLAAgreement | |
| `vendored_via` | ExternalDependency → ThirdPartyService | |

---

## 7. Cross-Cutting: How Lossless Is Preserved per Layer

| Layer | Lossless because… | Lossy fallback |
|-------|-------------------|---------------|
| AST (functions/classes/types) | Reconstructible from `source_uri` + `source_range`; properties mirror AST nodes | None; this layer is the ground truth |
| Schemas (DB / OpenAPI / GraphQL / Proto) | `raw_payload` stored verbatim alongside parsed structure | Parser version mismatch — track `extractor.version` |
| Configuration | `ConfigKey` retains literal default + every override per env | Computed runtime values (record as `derivation: 'dynamic'`) |
| Tests | `TestAssertion.subject_text/expected_text` verbatim; behavior captured by `asserts` edge | Behavioral test outcomes (use `CoverageReport` separately) |
| PRDs / ADRs | `body_text` + `body_hash`; section anchors preserved | Embedded media (link out) |
| Tickets | `raw_payload` from source system retained | Comment threads — store as `Discussion` children |
| Design (Figma) | Frame IDs + tokens stored; image is link-only with checksum | Pixel-level rendering |
| Tribal knowledge | `NarrativeNote.body` verbatim + `anchored_to[]` | Authoritative interpretation (record as `confidence`) |

---

## 8. Worked Example — End-to-end Trace of One Feature

**Feature:** "Allow customers to schedule recurring orders."

```
Feature(name="Recurring Orders") 
  ─belongs_to_epic→ Epic(name="Subscription Commerce")
  ─delivered_in→ Release(v2024.06)
  ─owned_by→ Team(name="Checkout")

Feature ─documented_in→ PRD(title="Recurring Orders PRD v3")
PRD ─contains→ PRDSection(heading="Cadence rules")
                 │
                 ├─derives→ BusinessRule(id="REC-01",
                 │           statement="Cadence must be one of 7d/14d/30d")
                 │
                 └─derives→ DataAssumption(statement="next_run_at always > now()")

PRD ─contains→ PRDSection(heading="Acceptance criteria")
PRDSection ─derives→ AcceptanceCriterion(
                       given="active subscription",
                       when="next_run_at <= now()",
                       then="order is created and next_run_at advances by cadence")

Feature ─tracked_by→ Ticket(LIN-4821) ─assigned_to→ Person(Aisha)
Ticket ─introduced_in_pr→ PullRequest(#882) ─merged_into→ Branch(main)

PullRequest ─changes→ {
  Migration(20240612_add_subscriptions),
  ORMEntity(Subscription),
  HTTPEndpoint(POST /subscriptions),
  Component(SubscriptionPlanPicker),
  ContractEndpoint(openapi: POST /subscriptions),
  TestCase(creates subscription with valid cadence),
}

Migration ─migrated_by→ Table(subscriptions)
Table(subscriptions) ─has_column→ Column(cadence_days)
Column(cadence_days) ─constrained_by→ CheckConstraint("cadence_days IN (7,14,30)")
                  ↑
                  └─governs (from BusinessRule REC-01) ✓ invariant enforced at DB

ORMEntity(Subscription) ─maps_to_table→ Table(subscriptions)
ORMEntity ─models_concept→ DomainConcept(name="Subscription")
DomainConcept ─defined_by→ GlossaryTerm(term="Subscription")

HTTPEndpoint(POST /subscriptions)
  ─handles← Handler(createSubscription)
  ─implements_contract→ ContractEndpoint(openapi: POST /subscriptions)
  ─validates_against→ Schema(CreateSubscriptionRequest)
  ─requires_permission→ Permission("subscriptions.create")
  ─writes_to→ Table(subscriptions)
  ─emits→ Event("subscription.created")
  ─instrumented_by→ MetricDefinition("subscriptions_created_total")
  ─assumes→ DataAssumption("next_run_at always > now()")
  ─implements_acceptance_criterion→ AcceptanceCriterion(given="...")

Event("subscription.created")
  ─consumed_by→ EventHandler(scheduleNextOrder)
  ─schema_id→ Schema(SubscriptionCreatedV1)

JobDefinition(processDueSubscriptions)
  ─scheduled_by→ CronSchedule("*/5 * * * *")
  ─reads_from→ Table(subscriptions)
  ─emits_event→ Event("order.created.recurring")

Component(SubscriptionPlanPicker)
  ─routes_to← Route(/account/subscriptions/new)
  ─uses_hook→ CustomHook(useCreateSubscription)
  ─tracks_event→ AnalyticsEvent("subscription_plan_selected")
  ─binds_form→ Form(NewSubscriptionForm)
  ─validated_by→ Schema(CreateSubscriptionRequest)

TestCase ─tests→ HTTPEndpoint(POST /subscriptions)
TestCase ─verifies← ContractTest → ContractEndpoint(openapi: POST /subscriptions)

ADR(0042: "Use DB-level cadence constraint over app validation")
  ─decided_in← Decision
  ─explains→ CheckConstraint("cadence_days IN (7,14,30)")

# Drift detection (later): if someone ALTER TABLE drops the constraint…
ChangeEvent(commit=abc123, kind=modified, node=CheckConstraint)
  → DriftSignal(
      anchor=BusinessRule(REC-01),
      drift_kind=invariant_no_longer_enforced,
      severity=high)
  → Surfaces in "context health" feed; alerts owning team.
```

The graph above answers, *without retrieval over text*:
- "What endpoints implement the recurring-orders PRD?" → walk `Feature.documented_in→PRD` then back via `PRDSection.derives→AcceptanceCriterion ←implements_acceptance_criterion← Endpoint`.
- "What breaks if we change the contract for `POST /subscriptions`?" → traverse `ContractEndpoint ←implements_contract← Endpoint` and `←verifies← ContractTest` and `←tracks_event← Component`.
- "Why does the cadence column have that constraint?" → `CheckConstraint ←explains← ADR-0042`.
- "Is anything stale?" → query `DriftSignal where severity >= medium`.

---

## 9. Open Questions / TODO for v0.2

1. **Multi-language conventions.** Define language-specific `derivation` extractors (TypeScript/Python/Go/Rust/Java/Kotlin/Swift) and any extra node attributes per language.
2. **Mobile-specific nodes.** iOS `ViewController`, Android `Activity`/`Fragment`, React Native bridge modules — likely a §4.6 sibling section.
3. **ML/Notebook artifacts.** `Notebook`, `Cell`, `Model`, `Dataset`, `Pipeline` if in scope.
4. **Identity resolution across systems.** A `Person` may map to N vcs identities, M Slack users, etc. — formalize as `Identity → Person` with `merge_evidence`.
5. **Permission model for the graph itself.** Reuse §4.14 with edges `viewable_by`/`editable_by` so query results respect source-system ACLs.
6. **Snapshot & diff format.** Concrete schema for `ChangeEvent.semantic_diff` so the "what changed in the model" feed is queryable.
7. **External payload retention policy.** When `raw_payload` is large (Figma frames, PDFs), where does it live (object storage with content-addressed key)?
8. **Versioning of the schema itself.** Schema migrations are inevitable; reserve a `SchemaVersion` node and migration log.
