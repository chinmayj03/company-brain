# Code Context — Refined Schema (v0.2 extension to schema-v0.1.md)

**Scope:** Adds the layer above AST/LSP. v0.1 captured *what the code is*. This document captures *what the code does, what it touches, what it promises, why it exists in this form, and what idioms govern it*. Without these layers, a graph is still a richer text search; with them, it can answer behavioral and intent questions.

**Lossless principle for derived semantics:** Several properties below are *inferred* (e.g., effect classification, complexity metrics, idiom detection). To stay lossless we record:
1. The literal evidence (e.g., "calls `fs.writeFile` at file:line"),
2. The inference rule and analyzer version that produced the conclusion,
3. A confidence score.

We never overwrite extracted facts with inferred ones — both coexist. Extracted facts (`derivation: 'ast' | 'lsp' | 'config'`) take precedence over inferred ones (`derivation: 'static_analysis' | 'llm' | 'dynamic'`) at query time.

---

## 1. Effects & Purity

The single most useful annotation a function can carry. Attach `EffectProfile` to every Function/Method.

### Node: `EffectProfile`

```ts
type EffectProfile = NodeEnvelope & {
  attributes: {
    pure: boolean;                       // no observable side effects, deterministic
    determinism: 'deterministic' | 'monotonic' | 'nondeterministic';

    reads: {
      fs?: boolean;       net?: boolean;      db?: boolean;
      env?: boolean;      time?: boolean;     random?: boolean;
      process?: boolean;  dom?: boolean;      storage?: boolean;
    };
    writes: {
      fs?: boolean;       net?: boolean;      db?: boolean;
      env?: boolean;      process?: boolean;  dom?: boolean;
      storage?: boolean;  cache?: boolean;
    };

    side_effect_kinds: Array<
      'network' | 'db_write' | 'log' | 'metric' | 'event_emit' |
      'file_write' | 'queue_publish' | 'cache_invalidate' | 'dom_mutation' |
      'navigation' | 'process_signal'
    >;

    idempotency: 'idempotent' | 'idempotent_with_key' | 'not_idempotent' | 'unknown';
    idempotency_key_path?: string;       // e.g., "request.headers['Idempotency-Key']"

    blocking: boolean;                   // CPU/IO blocking vs async
    can_throw: Array<{ error_class_id: string; conditional?: string }>;
    can_panic: boolean;

    observability_emissions: {
      logs: string[];      // LogStatement ids
      metrics: string[];   // MetricDefinition ids
      traces: string[];    // TraceSpan ids
      events: string[];    // Event ids
    };

    external_calls: Array<{
      target_id: string;   // ExternalAPI / ThirdPartyService
      method?: string;
      timeout_ms?: number;
      retry_id?: string;
    }>;

    // Evidence — every claim above traces back to a call site
    effect_evidence: Array<{
      claim: string;       // e.g., "writes to db"
      call_site: SourceRange;
      called_symbol_id?: string;
      derivation: 'ast' | 'static_analysis' | 'annotation' | 'llm';
    }>;
  };
};
```

### Edges
- `has_effect_profile`: Function → EffectProfile (1-1)

### Why lossless
Every claim in `EffectProfile` is backed by an `effect_evidence[]` entry pointing to a literal call site, so the profile is reproducible from source. Inferences (e.g., "this function is pure") are explicit and revisitable.

---

## 2. Intra-procedural Control & Data Flow

v0.1 ended at "function calls function." Real understanding needs the structure inside a function.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `BasicBlock` | id, function_id, source_range, instructions_hash |
| `Branch` | function_id, predicate_text, predicate_hash, true_block_id, false_block_id, kind (if/switch/ternary/guard) |
| `Loop` | function_id, kind (for/while/do/recursion), bound (known/unknown), body_block_id, induction_var? |
| `EarlyReturn` | function_id, source_range, returned_value_text, condition_text? |
| `GuardClause` | function_id, predicate_text, raises?, returns? |
| `CatchHandler` | function_id, source_range, error_class_ids[], rethrows |
| `FinallyBlock` | function_id, source_range, side_effect_kinds[] |
| `AwaitPoint` | function_id, source_range, awaited_expr_text, can_throw[] |
| `YieldPoint` | function_id, source_range, kind (value/delegated) |
| `ResourceAcquisition` | function_id, source_range, resource_id, scope (function/request/process) |
| `ResourceRelease` | function_id, source_range, resource_id |

### Edges (intra-function CFG/DFG)

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `flows_to` | BasicBlock → BasicBlock | CFG edge |
| `branches_to` | Branch → BasicBlock | with `condition_truth` prop |
| `data_flows` | Variable/Param/Field → Variable/Param/Sink | with `kind: read/write/passed/returned/escaped/copied` |
| `taint_flows` | UntrustedSource → Sink | tracks input from external sources to sensitive sinks |
| `originates_from` | LocalVariable → Param/Literal/CallReturn/ExternalRead | provenance of values |
| `consumed_at` | Variable → CallSite/Branch/ReturnStatement | |
| `escapes_via` | Variable → Return/CallArg/StoreWrite | escape analysis |

### Sample lossless query enabled
> "Does any user-supplied string ever reach `db.raw()` without passing through a `Sanitizer` or being bound as a parameter?"
> Walk: `HTTPEndpoint.handler ─param→ Variable ─taint_flows→* ... → CallSite(db.raw)` minus paths where edge `sanitizes` intervenes.

---

## 3. Trust Boundaries & Authorization Topology

Every codebase has implicit trust zones. Make them explicit.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `TrustZone` | name (untrusted_input/authenticated/admin/internal/database/third_party), entry_points[], policy |
| `TrustBoundary` | from_zone_id, to_zone_id, mediating_function_ids[] (validators, authenticators) |
| `AuthCheck` | name, mechanism (session/jwt/api_key/oauth/sigv4), required_grants[], applies_to[], strict (deny-by-default) |
| `Sanitizer` | function_id, sanitizes_for (sql/html/shell/path/regex), output_type_id |
| `Validator` | function_id or schema_id, predicate_text, rejects_on_fail (true/false) |
| `Capability` | name (e.g., "billing.read"), description |
| `AuthorizationDecisionPoint` | function_id, source_range, predicate, allow/deny semantics |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `enters_zone` | Endpoint/Function → TrustZone | with `via_boundary_id` |
| `crosses_boundary` | Function/CallSite → TrustBoundary | |
| `enforces_check` | Middleware/Function → AuthCheck | |
| `requires_capability` | Endpoint/Function → Capability | |
| `bypasses_check` | Function → AuthCheck | rare, must carry `justification` and `approver_id` — flagged for security |
| `sanitizes` | Function → Type/Schema/Sink | |
| `assumes_trusted` | Function → Parameter/Field | explicit "this is already trusted" annotation |
| `delegates_authz_to` | Function → AuthorizationDecisionPoint | |

### Why this matters
This is what lets the system answer:
> "What endpoints write to the `audit_log` table without an `enforces_check → AuthCheck(name='admin')`?"
> "Which `Sanitizer` is missing on the user-feedback path?"

---

## 4. Concurrency & State

Implicit in code, often invisible in graphs, the source of most production weirdness.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `ConcurrencyContext` | name, kind (event_loop/thread/coroutine/actor/process), max_parallelism |
| `Lock` | name, kind (mutex/rwlock/optimistic/distributed), scope (process/cluster), held_by_function_ids[], contention_class |
| `AtomicOperation` | function_id, source_range, target_id, op (cas/inc/swap) |
| `Transaction` | name, kind (db/saga/local/distributed), isolation_level, scope_function_id, propagation (required/new/nested) |
| `RaceWindow` | id, between_writes (write_a_id, write_b_id), risk_level, scenario |
| `StateMachine` | name, owner_entity_id, states[], transitions[], terminal_states[] |
| `StateTransition` | machine_id, from_state, to_state, trigger_event_id, guard_predicate, side_effect_function_id |
| `Saga` | name, steps[] (each with compensating_action_id), coordination (orchestration/choreography) |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `runs_in_context` | Function → ConcurrencyContext | |
| `acquires` | Function → Lock | with `acquisition_order` |
| `releases` | Function → Lock | |
| `participates_in_tx` | Function → Transaction | with `role: opens/joins/commits/rolls_back` |
| `transitions_state` | Function → StateTransition | |
| `awaits` | Function → AwaitPoint | |
| `spawns` | Function → JobDefinition/WorkerPool | |
| `enqueues` | Function → QueueTopic | with `partition_key`, `dedup_key`, `delay_ms` |
| `compensates` | Function → SagaStep | |

### Sample lossless query
> "Find functions that write to two different DB partitions inside the same Transaction without using a saga." → query for `Function ─writes_to→ Table` × 2 with same `participates_in_tx → Transaction` and no outgoing `compensates` edge.

---

## 5. Resource Lifetimes

Resources (DB connections, file handles, leases, locks) have lifetimes — leaks come from broken pairings.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `Resource` | name, kind (db_conn/file/socket/lock/lease/http_client/cursor), pool_id?, finite (true/false) |
| `ResourcePool` | name, max_size, acquire_timeout_ms, idle_eviction_ms |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `acquires_resource` | Function → Resource | `at_block_id`, `binds_to_var` |
| `releases_resource` | Function → Resource | `at_block_id` |
| `owns_resource` | Class/Module → Resource | for fields holding resources |
| `leak_potential` | Function → Resource | inferred — paths where acquired but no matching release on all CFG paths; `paths_missing_release[]` |

---

## 6. API Surface & Stability Tiers

Not all symbols are equal. Some are public commitments; others are internal scaffolding.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `APISurface` | name, tier (public/sdk/internal/experimental/deprecated), audience, stability_promise (semver/none) |
| `SemverContract` | symbol_id, version_introduced, version_last_changed, breaking_in[], additive_in[] |
| `DeprecationMarker` | symbol_id, deprecated_in_version, sunset_in_version, replacement_id, migration_doc_id |
| `BreakingChange` | symbol_id, version, kind (signature/behavior/removal/contract), description, migration_steps[] |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `exposes` | Package/Module → Symbol | with `as_name`, `tier` |
| `surface_tier` | Symbol → APISurface | |
| `stable_since` | Symbol → ReleaseVersion | |
| `breaking_change_in` | Symbol → BreakingChange | |
| `replaced_by` | Symbol → Symbol | |
| `wraps_legacy` | Symbol → Symbol | adapter pattern indicator |

### Why it matters
"Can I refactor this safely?" becomes a query: if `surface_tier` is `internal`, yes; if `public`, only with a major version bump or a `BreakingChange` entry.

---

## 7. Cross-Process & Cross-Language Boundaries

Graphs that stop at language boundaries miss the most important edges in microservice systems.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `Boundary` | name, kind (http/grpc/queue/ffi/ipc/serialization/webhook), schema_id, encoding (json/proto/msgpack/cbor/avro), versioning_strategy |
| `SerializationContract` | schema_id, version, compatibility (forward/backward/full/none) |
| `TypeMapping` | language_a, type_a, language_b, type_b, encoding, lossy_dimensions[] |
| `RPCBinding` | client_function_id, server_handler_id, contract_id, transport |
| `FFIBinding` | foreign_symbol, native_signature, marshaling, unsafe |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `crosses_process_boundary` | Function/CallSite → Boundary | |
| `serializes_with` | Function → SerializationContract | direction: in/out |
| `binds_to_remote` | Function → RPCBinding | |
| `interops_with` | Function → FFIBinding | |
| `same_logical_op_as` | Function → Function | links client stub and server handler across services |

### Why it matters
Lets queries cross repos: "Who calls `payments-service.charge`?" returns clients across all services, not just files in this repo.

---

## 8. Code Generation & Provenance

Generated files are a permanent source of confusion for both humans and agents.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `Generator` | name, version, command, input_kind (proto/openapi/graphql/orm-schema/figma-tokens), output_template |
| `GenerationSource` | source_artifact_id (proto file / OpenAPI spec / Figma file), commit, hash |
| `GeneratedFile` | file_id, generator_id, source_id, generated_at_commit, marker_comment |
| `GenerationRule` | when_changed (source pattern), regenerate (output pattern), command |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `generated_from` | File → GenerationSource | |
| `generated_by` | File → Generator | |
| `regenerates` | Generator → File pattern | declares ownership |
| `do_not_edit` | GeneratedFile → marker | hard signal: edits will be overwritten |
| `co_evolves_with` | GeneratedFile → GenerationSource | for drift detection |

### Why it matters
Stops agents from editing files that will be overwritten on next build, and identifies when generated artifacts have drifted from their source.

---

## 9. Code Intent — The "Why"

The biggest gap. Most code lacks any record of *why it is the way it is*.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `Intent` | statement (in business terms), authored_by, authored_at_commit, scope_id (function/class/module) |
| `Rationale` | statement (why this approach vs alternatives), alternatives_considered[], scope_id |
| `OriginEvent` | scope_id, kind (introduced/refactored/extracted/inlined/renamed/extracted_from), commit, author, ticket_id?, pr_id? |
| `OrphanReason` | scope_id, kind (no_pr_link/no_ticket_link/no_test_coverage/no_recent_use), detected_at_commit |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `intent_of` | Intent → Function/Class/Module | |
| `rationale_for` | Rationale → Decision/Function/Class | |
| `originated_at` | Function/Class → OriginEvent | |
| `serves_purpose` | Function/Class → Capability/Feature/UseCase | |
| `flagged_orphan` | Function/Class → OrphanReason | |

### The Motivation Chain (REQUIRED traversal)

For any non-trivial Function, the following chain should be reachable:
```
Function 
  ─originated_at→ OriginEvent ─in_pr→ PullRequest 
  ─tracked_by→ Ticket 
  ─implements_story→ UserStory 
  ─belongs_to→ Epic ─delivers→ Feature 
  (─enacts_bet→ StrategicBet — see business-context-v0.2.md)
```

Functions where this chain breaks → `flagged_orphan`. Surfaces in a "code without purpose" report. This is the *forcing function* for keeping business linkage alive.

---

## 10. Code Quality Dimensions

Computed metrics attached as attributes to existing nodes; no new node types needed.

| Metric | Attached to | Derivation | Lossless? |
|--------|-------------|------------|-----------|
| `cyclomatic_complexity` | Function | AST + standard formula | Deterministic |
| `cognitive_complexity` | Function | AST + Sonar formula | Deterministic |
| `lines_total` / `lines_code` / `lines_comment` | Function/File | Tokenizer | Deterministic |
| `param_count` / `return_arity` | Function | AST | Deterministic |
| `nesting_depth_max` | Function | AST | Deterministic |
| `churn_30d` / `churn_90d` | File/Function | git log windowed | Deterministic |
| `age_commits` / `age_days` | Symbol | git log | Deterministic |
| `test_density` | Function | Coverage report joined with size | Derived |
| `bus_factor` | Symbol | Blame distribution | Derived |
| `last_touched_by` | Symbol | git blame | Deterministic |
| `hotspot_score` | File | churn × complexity normalized | Derived |
| `coupling_in` / `coupling_out` | Module/Class | Reference edges count | Deterministic |
| `instability` | Module | `coupling_out / (in + out)` | Deterministic |

These attach to nodes as `attributes.metrics: { ... }` with `metrics_computed_at_commit` for invalidation.

---

## 11. Patterns & Idioms in Use

Codebases standardize on patterns. An agent that knows the local patterns generates code that fits.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `IdiomCatalog` | name (e.g., "Result type for fallible ops"), description, exemplars[] (function ids), enforcement (lint/codereview/none) |
| `IdiomInstance` | catalog_id, location_id, fidelity (canonical/acceptable/divergent) |
| `Antipattern` | name, severity, detection_rule, examples[] |
| `Convention` | scope (naming/file_layout/folder_structure), rule, examples |
| `DependencyInjectionBinding` | container, abstraction_id, implementation_id, scope (singleton/scoped/transient) |
| `ExtensionPoint` | name, contract_id, registered_implementations[] |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `follows_idiom` | Function/Module → IdiomCatalog | with `fidelity` |
| `violates_idiom` | Function/Module → IdiomCatalog | |
| `exemplifies` | Function → IdiomCatalog | canonical examples |
| `uses_antipattern` | Function → Antipattern | |
| `bound_via` | Class → DependencyInjectionBinding | |
| `extends_via_extension_point` | Class/Function → ExtensionPoint | |
| `follows_convention` | * → Convention | |

### Why it matters
"Generate a new endpoint in the style of the codebase" needs to know the codebase's conventions: error handling style (Result vs throw), DI pattern, repository pattern, validation library, response shape. These are queryable, not vibes.

---

## 12. Cross-cutting Concerns (often invisible)

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `LoggingPolicy` | scope, required_levels[], required_attributes[], pii_redaction_rules[] |
| `TracingPolicy` | scope, required_spans[], required_attributes[], sampling_strategy |
| `ErrorTaxonomy` | name, hierarchy[], http_mapping{}, user_facing |
| `RetryPolicy` | name, kind (fixed/exponential/jittered), max_attempts, base_ms, max_ms, retryable_errors[] |
| `CircuitBreaker` | name, threshold_failures, window_ms, recovery_ms, half_open_probes |
| `Bulkhead` | name, max_concurrent, queue_depth, reject_strategy |
| `Timeout` | name, deadline_ms, propagates (true/false) |
| `Cache` | name, kind, ttl, key_pattern, invalidation_triggers[] |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `subject_to_logging_policy` | Function/Module → LoggingPolicy | |
| `wrapped_by` | Function/CallSite → CircuitBreaker/RetryPolicy/Bulkhead/Timeout | with `order` if stacked |
| `cached_by` | Function → Cache | |
| `error_classified_by` | Function → ErrorTaxonomy | |

### Why it matters
"Show me every external call without a timeout" or "find functions that log a request body containing PII" become deterministic queries.

---

## 13. Symbol-Level Versioning (the temporal granularity v0.1 understated)

v0.1 had `valid_from_commit` / `valid_to_commit` on nodes. Refine for symbols specifically:

### Symbol-version nodes
- `SymbolVersion` — symbol_id, version_label, valid_from_commit, valid_to_commit, signature_hash, body_hash, breaking_from_prev (bool with reason)

This lets you ask: "What did `chargeCard` look like at commit `abc`?" without re-running git checkout — the structural facts are stored.

### Edges
- `version_of` SymbolVersion → Symbol
- `succeeds` SymbolVersion → SymbolVersion
- `signature_changed_in` SymbolVersion → Commit; with `kind: param_added/param_removed/return_changed/throw_added`

---

## 14. Integration Points (How These Layers Connect to v0.1)

| v0.2 Node | Attaches to v0.1 Node | Via Edge |
|-----------|----------------------|----------|
| `EffectProfile` | `Function` | `has_effect_profile` |
| `BasicBlock` / `Branch` / `Loop` | `Function` | `contains` (function-scoped) |
| `TrustBoundary` / `AuthCheck` | `Endpoint`, `Middleware` | `enforces_check`, `crosses_boundary` |
| `Lock` / `Transaction` / `StateMachine` | `Function`, `Service` | `acquires`, `participates_in_tx`, `transitions_state` |
| `Resource` | `Function`, `Service` | `acquires_resource`, `releases_resource` |
| `APISurface` / `BreakingChange` | `Function`, `Class`, `Endpoint`, `ContractEndpoint` | `surface_tier`, `breaking_change_in` |
| `Boundary` / `RPCBinding` | `Function`, `ContractEndpoint` | `crosses_process_boundary` |
| `Generator` / `GeneratedFile` | `File` | `generated_by`, `generated_from` |
| `Intent` / `Rationale` / `OrphanReason` | `Function`, `Class` | `intent_of`, `rationale_for`, `flagged_orphan` |
| `IdiomCatalog` / `Antipattern` | `Function`, `Module` | `follows_idiom`, `uses_antipattern` |
| `RetryPolicy` / `CircuitBreaker` / `Cache` | `Function`, `Endpoint` | `wrapped_by`, `cached_by` |

---

## 15. Worked Trace — `createSubscription` with the Refined Layer

Continuing the v0.1 example, now with code-context refinement applied to the same endpoint:

```
HTTPEndpoint(POST /subscriptions)
  ─handles← Handler(createSubscription)
  ─enters_zone→ TrustZone(authenticated)
  ─crosses_boundary→ TrustBoundary(untrusted_input → authenticated)
        mediated by Middleware(jwtAuth) ─enforces_check→ AuthCheck(session)
  ─requires_capability→ Capability("billing.write")
  ─wrapped_by→ RetryPolicy(none)        # writes are not retried at edge
  ─wrapped_by→ Timeout(5000ms)
  ─cached_by→ none

Handler(createSubscription) ─has_effect_profile→ EffectProfile {
  pure: false,
  determinism: 'nondeterministic',     # uses time + db
  reads: { time: true, db: true },
  writes: { db: true },
  side_effect_kinds: ['db_write','event_emit','metric','log'],
  idempotency: 'idempotent_with_key',
  idempotency_key_path: "request.headers['Idempotency-Key']",
  blocking: false,
  can_throw: [
    { error_class_id: 'ValidationError' },
    { error_class_id: 'PaymentDeclined' },
    { error_class_id: 'DuplicateSubscription', conditional: 'unique_violation' }
  ],
  observability_emissions: {
    metrics: ['subscriptions_created_total','subscription_creation_latency_ms'],
    logs:    ['log:subscription_created'],
    events:  ['event:subscription.created'],
  },
  external_calls: [{ target_id: 'stripe', method: 'subscriptions.create', timeout_ms: 4000 }],
  effect_evidence: [
    { claim: 'writes db', call_site: <range>, called_symbol_id: 'subscriptionsRepo.insert' },
    { claim: 'external call', call_site: <range>, called_symbol_id: 'stripe.subscriptions.create' },
  ],
}

Handler ─participates_in_tx→ Transaction(subscriptionCreate, isolation: 'read_committed')
Transaction ─commits_after→ external_call(stripe.subscriptions.create)  # ⚠ smell: external call inside tx
                                                                          → flagged as Antipattern("external_call_in_tx", severity: high)

Handler ─intent_of← Intent("Persist a recurring purchase intent for the customer and 
                            schedule downstream order generation.")
Handler ─rationale_for← Rationale("DB constraint enforces cadence values per ADR-0042; 
                                  app-level validation is defense-in-depth, not source of truth.")
Handler ─originated_at→ OriginEvent(commit=abc123, in_pr=#882, ticket=LIN-4821)
Handler ─follows_idiom→ IdiomCatalog("Repository pattern with explicit Tx scope")
Handler ─follows_idiom→ IdiomCatalog("Result-style error responses via ApiError")

Handler ─surface_tier→ APISurface(internal)        # endpoint is public, handler symbol is internal
ContractEndpoint(POST /subscriptions) ─surface_tier→ APISurface(public, sdk)
```

Now the agent answering "is it safe to remove the external call from inside this transaction?" has every fact it needs — flagged antipattern, transaction scope, retry policy, idempotency key — without re-reading the file.

---

## 16. Open Questions for v0.3

1. **Dynamic-only effects.** Some effects only manifest at runtime (reflection, eval, plugin loading). Capture as `dynamic_effect_observed` with confidence inversely proportional to coverage.
2. **Multi-version coexistence.** When v1 and v2 of an endpoint coexist, both `SymbolVersion`s are valid simultaneously. The schema supports it; query patterns need formalizing.
3. **Inferred-vs-asserted reconciliation.** When LLM-inferred intent contradicts an `Intent` annotation, surface as a `DriftSignal` rather than silently overwriting either.
4. **Granularity for intra-procedural CFG/DFG.** Storing every `BasicBlock` for every function can be expensive. Tier: store full CFG only for functions matching `complexity > N` or `surface_tier = public`.
5. **Effect propagation.** If function A's effect profile includes "calls B", should A inherit B's effects? Probably yes, transitively closed but with explicit `transitive: true` flag and the path retained.
