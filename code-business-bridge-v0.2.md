# Code↔Business Bridge — Business Context Embedded in Code (v0.2)

**Scope:** This is the third refinement layer. v0.1 modeled external business artifacts (PRDs, ADRs, tickets). business-context-v0.2.md added the operating layer (strategy, pricing, customer evidence). This document captures **business context that lives inside the code itself** — the meaning encoded in identifiers, constants, conditionals, annotations, test names, configuration files, database columns, commit messages, and folder structure.

**Why this layer is mission-critical.** In most companies, code is the *operating source of truth* for business behavior; PRDs go stale within weeks of merge. The business invariants enforced in production live as conditionals and constants, not in Notion. An extractor that ignores these is reading a business with the documentation taped over its mouth.

**Relationship to the other layers:**

```
   ┌──────────────────────────────────────────┐
   │ Pure CODE semantics (code-context-v0.2)  │  what code does (effects, types, flow)
   │                                          │
   │   ┌───────────────────────────────────┐  │
   │   │ CODE↔BUSINESS BRIDGE (this doc)   │  │  what code reveals about the business
   │   │                                   │  │
   │   │  ┌────────────────────────────┐   │  │
   │   │  │ Pure BUSINESS context       │  │  │  what the business is (PRDs, plans,
   │   │  │ (business-context-v0.2)     │  │  │  strategy, customers, operations)
   │   │  └────────────────────────────┘   │  │
   │   └───────────────────────────────────┘  │
   └──────────────────────────────────────────┘
```

The bridge layer is what **closes the loop**: it takes facts derived from raw code (left) and connects them to the business model (right), enabling drift detection both ways.

---

## 1. Lossless Principle for Inferred Business Semantics

Most facts in this layer are *inferred* from code patterns (identifier name matches glossary; magic number resembles a price; conditional branches on a plan name). To stay lossless we always store:

1. **The literal source.** The actual identifier text, the literal constant value, the literal conditional expression, the verbatim string. Never replaced.
2. **The inference rule.** "matched glossary term `Subscription` via camelCase split with stemmed comparison."
3. **The candidate links.** Multiple candidates with confidence scores, never collapsed to "the answer" silently.
4. **The pattern version.** The extraction rule has a version, so re-extraction with a better rule can be re-run without losing prior conclusions.
5. **Negative facts.** When extraction *attempted* to find a business mapping and failed, that failure is recorded (`unbound_constant`, `orphan_business_concept`) — silence is information.

Every node in this layer therefore carries: `literal_evidence`, `inference_rule_id`, `inference_rule_version`, `candidate_links[]`, `confidence`, plus the standard `NodeEnvelope` from schema-v0.1.md.

---

## 2. Categories of Business Context Embedded in Code

Thirteen categories. Each gets a node taxonomy, edge taxonomy, extraction strategy, and drift signal definition.

| # | Category | Where it hides |
|---|----------|----------------|
| 1 | Identity & naming | function/class/variable/type names |
| 2 | Constants & magic numbers | numeric literals, named constants |
| 3 | String resources | error messages, plan names, event names, URLs, log templates |
| 4 | Annotations & decorators | `@RequiresEntitlement`, `@PII`, `@Audit` |
| 5 | Conditionals encoding rules | `if user.plan === 'enterprise'`, `switch (region) { case 'EU': ... }` |
| 6 | Embedded documentation | docstrings, TODOs, FIXMEs, deprecation notes |
| 7 | Configuration & data files | pricing.json, fixtures, seeds, feature-flag manifests |
| 8 | Test names as living specs | `it("should not refund after 30 days on Pro plan")` |
| 9 | Database schema encoding | column names, comments, status enums, audit columns |
| 10 | Commit/branch/PR metadata | conventional commit messages, branch initiative names |
| 11 | Module/folder organization | `billing/`, `compliance/`, bounded contexts |
| 12 | Error codes & status conventions | `INSUFFICIENT_FUNDS`, `402 Payment Required`, `451` |
| 13 | Implicit invariants & guards | `assert(balance >= 0)`, runtime checks without annotation |

---

## 3. Category 1 — Identity & Naming

The single richest signal. A function called `chargeEnterpriseRecurring` carries an enormous amount of business context that an AST extractor will throw away if not actively decoded.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `IdentifierTokenization` | symbol_id, raw_name, tokens[] (after camelCase/snake_case split), normalized_tokens[] (stemmed/lemmatized) |
| `DomainTermMatch` | symbol_id, matched_term_id (→ GlossaryTerm), matched_via_token, match_kind (exact/stem/synonym/abbreviation), confidence |
| `BusinessConceptType` | type_id, mapped_concept_id (→ DomainConcept), evidence (name_match/structural/annotation), confidence |
| `ActorActionPattern` | function_id, actor (extracted), verb, object — e.g., `(actor: customer, verb: charge, object: card)` |
| `BoundedContextSignal` | symbol_id, candidate_context_ids[], evidence (folder/import/dependency_cluster), confidence |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `references_domain_term` | Symbol → GlossaryTerm | with `via_token`, `confidence` |
| `embodies_concept` | Class/Type → DomainConcept | structural mapping |
| `participates_in_capability` | Module/Class → Capability | inferred from naming + folder |
| `tokenized_as` | Symbol → IdentifierTokenization | |

### Extraction strategy

1. Tokenize every identifier (camelCase/snake_case/kebab-case split).
2. Normalize (lowercase, lemmatize, expand known abbreviations: `qty→quantity`, `usr→user`).
3. Match against `GlossaryTerm` and `DomainConcept` via exact + stem + synonym.
4. Detect actor-verb-object patterns in function names (POS tagging).
5. Cluster co-occurring tokens to seed glossary suggestions for unmatched terms (`extracted_term_candidates`).

### Drift signal
- `business_concept_orphan` — type with high-confidence business-sounding name but no `embodies_concept` edge → either glossary missing the term, or naming is misleading. Both are actionable.

---

## 4. Category 2 — Constants & Magic Numbers

Every numeric literal in production code encodes a decision. Most are unowned.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `BusinessConstant` | symbol_id (Constant), name, literal_value, type, unit (currency/days/count/bytes/...), classification (price/limit/threshold/window/quota/discount/feature_count) |
| `MagicNumber` | call_site_id, source_range, literal_value, type, surrounding_context_text, classification_candidate, confidence |
| `PricingConstant` | constant_id, currency, amount, sku_candidate, applies_to_plan_candidate |
| `ThresholdConstant` | constant_id, threshold_kind (cap/floor/ratio/percent), governs_what_candidate |
| `TimeConstant` | constant_id, value, unit (sec/min/hour/day/...), kind (window/timeout/expiry/retention/cooldown/grace_period) |
| `RetryConstant` | constant_id, kind (max_attempts/base_delay/jitter), wraps_function_id |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `encodes_business_rule` | BusinessConstant → BusinessRule/Policy | inferred or explicit |
| `embeds_price` | PricingConstant → PricingTier/Sku | |
| `embeds_quota` | ThresholdConstant → QuotaDefinition | |
| `embeds_window` | TimeConstant → RetentionPolicy/Trial/RefundPolicy | |
| `unbound_constant` | MagicNumber → null | flag: literal with no business binding (anti-edge / sentinel) |

### Extraction strategy

1. Find every `Constant` node and every numeric/string literal in `Branch` predicates and call args.
2. Apply heuristics: `* 1000 * 60 * 60 * 24` patterns → time; `currency in scope` → price; `< N || > N` thresholds.
3. LLM-classify with surrounding context (3 lines + function signature + module path).
4. Cross-reference with `business-context-v0.2.md` nodes (`PricingTier`, `QuotaDefinition`, `Trial`, `RefundPolicy`) for candidate binding.

### Drift signals
- `unbound_constant` — magic number in production code with no `encodes_*` edge → either missing business node or a magic number that should be named.
- `pricing_inconsistency` — `embeds_price` value differs from authoritative `PricingTier.price_per_unit` → drift between code and pricing source of truth.
- `time_constant_inconsistent` — `TRIAL_DAYS = 14` in code but `Trial.length_days = 21` in business node → high-severity drift.

---

## 5. Category 3 — String Resources with Business Meaning

Strings are where brand voice, plan names, event taxonomies, and URL schemes live.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `ErrorMessageTemplate` | symbol_id or location, template_text, parameters[], surface (user/log/api), tone_match (→ BrandVoiceGuideline candidate) |
| `PlanIdentifierLiteral` | location, value, candidate_plan_ids[] |
| `SegmentIdentifierLiteral` | location, value, candidate_segment_ids[] |
| `FeatureFlagKey` (literal in code) | location, key, candidate_flag_id |
| `EntitlementKey` (literal in code) | location, key, candidate_entitlement_id |
| `EventNameLiteral` | location, name, candidate_analytics_event_id, schema_used (PascalCase/snake_case/dot.case) |
| `URLPathLiteral` | location, pattern, candidate_endpoint_id, encodes_business_path (segment/plan/operation) |
| `LogMessageTemplate` | location, template, level, classification (audit/business/diagnostic) |
| `RegexBusinessPattern` | location, pattern_text, classification (email/phone/credit_card/address/iban/...), validates_what_candidate |
| `EnumLiteralWithBusinessMeaning` | enum_member_id, value, candidate_lifecycle_stage_id, candidate_status_taxonomy |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `voice_compliant_in_code` | ErrorMessageTemplate → BrandVoiceGuideline | |
| `voice_violates_in_code` | ErrorMessageTemplate → BrandVoiceGuideline | with severity |
| `references_plan_literal` | PlanIdentifierLiteral → PricingPlan | |
| `references_entitlement_literal` | EntitlementKey → Entitlement | |
| `references_event_literal` | EventNameLiteral → AnalyticsEvent | |
| `audit_log_emission` | LogMessageTemplate → AuditEventDefinition | classification=audit |

### Extraction strategy

1. Walk every string literal in source.
2. Classify by pattern: regex (URL/path/key-with-dots/snake_case), keyword presence (matching plan names from `PricingPlan.name`), schema match (matching `AnalyticsEvent.name`).
3. Resolve to candidate business nodes.
4. For error messages, run brand-voice classifier (against `BrandVoiceGuideline`).
5. Detect inconsistent string conventions (event name in `dot.case` here, `PascalCase` there) → emit `naming_convention_drift`.

### Drift signals
- `unmapped_business_literal` — string that looks like a plan/entitlement/event identifier but doesn't match any business node.
- `stale_plan_reference` — code references plan name that's been sunset.
- `voice_violation_count` — emitted error messages failing brand-voice rules per file.

---

## 6. Category 4 — Annotations & Decorators with Business Semantics

The most explicit form of in-code business context. When present, treat as authoritative.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `BusinessAnnotation` | location, name, args, applies_to_id, kind (entitlement/permission/audit/pii/compliance/rate_limit/deprecated/experimental/feature_flag/ab_test) |
| `EntitlementAnnotation` | annotation_id, entitlement_key |
| `PIIAnnotation` | annotation_id, pii_class (email/name/phone/address/sensitive), masking_strategy_candidate |
| `AuditAnnotation` | annotation_id, event_name, fields_logged_candidate |
| `ComplianceAnnotation` | annotation_id, framework (GDPR/HIPAA/PCI/SOC2), control_id |
| `DeprecationAnnotation` | annotation_id, deprecated_since, sunset_date, replacement_id, migration_guide_id |
| `FeatureGateAnnotation` | annotation_id, flag_key, default_behavior |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `gated_by_entitlement` (asserted) | Function/Endpoint → Entitlement | from EntitlementAnnotation — high confidence |
| `handles_pii` (asserted) | Function/Field → PIIField | from PIIAnnotation |
| `audited_by` (asserted) | Function → AuditEventDefinition | from AuditAnnotation |
| `subject_to_compliance` (asserted) | Function/Endpoint → ComplianceRequirement | from ComplianceAnnotation |
| `deprecated_in_code` | Function/Class → DeprecationMarker | from DeprecationAnnotation |

### Extraction strategy

1. Parse all decorators/annotations/attributes per language (Python `@decorator`, TypeScript decorators, Java `@Annotation`, C# `[Attribute]`, Rust `#[derive]`).
2. Match against the codebase's known business-annotation catalog (curated per company).
3. For unrecognized annotations, capture as `BusinessAnnotation` with `kind=unknown` for review.
4. Reconcile asserted edges with inferred edges from other categories — annotation wins, but disagreement is recorded.

### Drift signals
- `pii_handler_unannotated` — function with strong PII evidence (calls `email`, `phone`, `ssn` columns) but no `@PII` annotation.
- `audit_gap` — function modifying critical state (writes to `audit_log` schema, processes payments) without `@Audit`.
- `entitlement_gate_drift` — `@RequiresEntitlement("foo")` references entitlement key not present in business `Entitlement` catalog.

---

## 7. Category 5 — Conditionals Encoding Business Rules

Where business rules live when there's no annotation framework.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `BusinessConditional` | branch_id (→ Branch), predicate_text, predicate_normalized, classification (plan_check/segment_check/region_check/entitlement_check/lifecycle_check/feature_flag_check/role_check/account_age/ab_variant) |
| `EligibilityCheck` | function_id, predicate_text, decides_what (access/discount/feature/upgrade), inputs[] |
| `DiscriminationLogic` | function_id, discriminator (segment/region/plan/cohort), branches[], legitimacy_review_status (none/reviewed/flagged) |
| `RegionalBranch` | branch_id, region_check_target_value, divergent_behavior_summary |
| `LegacyBranch` | branch_id, legacy_marker (e.g., `if (user.legacy_billing)`), candidate_grandfathering_rule |
| `KillSwitch` | branch_id, flag_or_config_key, default_state, controls_what |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `gates_for_plan` | BusinessConditional → PricingPlan | |
| `gates_for_segment` | BusinessConditional → CustomerSegment | |
| `gates_for_geography` | BusinessConditional → Geography | |
| `gates_for_entitlement` | BusinessConditional → Entitlement | |
| `gates_for_lifecycle_stage` | BusinessConditional → LifecycleStage | |
| `enforces_implicitly` | BusinessConditional → BusinessRule | inferred |
| `kill_switched_by` | Function → KillSwitch | |

### Extraction strategy

1. Walk every `Branch.predicate_text`.
2. Pattern-match against known business identifiers in scope: plan names, segment keys, region codes, feature flag keys, entitlement keys, role names.
3. Classify the branch: which business axis is it discriminating on?
4. Group divergent code paths by discriminator → `DiscriminationLogic` for review (especially geographic — this is where compliance lapses hide).
5. Detect legacy branches (`if (user.legacy_*)`, `if (account.created_before(...))`) → candidate grandfathering rules.

### Drift signals
- `gate_for_unknown_plan` — branches on a plan name not in `PricingPlan` catalog.
- `dark_geographic_divergence` — region-gated branches with no link to `JurisdictionalRule` or `Feature.unavailable_in` → why does this code path exist?
- `legacy_branch_undocumented` — `LegacyBranch` with no `candidate_grandfathering_rule` → tribal knowledge about old customers, soon to be lost.
- `dead_business_branch` — gates for a sunset plan/feature → cleanup candidate.

---

## 8. Category 6 — Embedded Documentation in Code

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `BusinessDocstring` | location, body_text (verbatim), classified_intent (what/why/how/warning), references_extracted (tickets/ADRs/business_terms) |
| `TODOWithContext` | location, body_text, ticket_ref?, person_mention?, deadline?, classification (bug/improvement/business_change/spike) |
| `FIXMEWithContext` | location, body_text, severity, ticket_ref?, business_implication |
| `DeprecationCommentNote` | location, body_text, replacement_hint, sunset_hint |
| `FileLicenseHeader` | file_id, license_id, copyright, attribution_required |
| `BusinessRationaleComment` | location, body_text — comments explaining "why this odd code", linked to Decision/ADR candidate |
| `CommentTicketReference` | location, ticket_external_id, kind (fix-for/per/see) |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `documents_intent_in_code` | BusinessDocstring → Function/Class | |
| `references_ticket_in_code` | CommentTicketReference → Ticket | |
| `references_adr_in_code` | Comment → ADR | |
| `surfaces_business_warning` | BusinessDocstring/FIXME → BusinessRule | |
| `proposes_change` | TODOWithContext → Ticket (often unlinked → candidate to file) | |

### Extraction strategy

1. Pull every comment + docstring; segment by kind.
2. Regex for ticket references (`#1234`, `LIN-1234`, `JIRA-XYZ-456`).
3. LLM-classify intent of long-form docstrings ("why" vs "what").
4. Match deprecation phrasing patterns.
5. Surface unbound TODOs as candidate ticket creation list.

### Drift signal
- `stale_ticket_reference` — comment references closed/moved ticket → comment is potentially obsolete.
- `unbound_TODO` — TODO with no ticket reference and no deadline → invisible work.
- `business_docstring_drift` — docstring describes behavior that the function's `EffectProfile` no longer matches.

---

## 9. Category 7 — Configuration & Data Files as Business Source-of-Truth

Often more authoritative than the code that uses them.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `BusinessConfigFile` | file_id, classification (pricing/plans/entitlements/feature_flags/regions/translations/runtime_policy), schema_id?, raw_payload (verbatim) |
| `EmbeddedPricingTable` | file_id, parsed_rows[], canonical_pricing_id_candidate |
| `EmbeddedPlansManifest` | file_id, plans[], links_to_PricingPlan_ids[] |
| `EmbeddedEntitlementsManifest` | file_id, entitlements[], links_to_Entitlement_ids[] |
| `EmbeddedRegionsManifest` | file_id, regions[], links_to_Geography_ids[] |
| `FixtureBusinessData` | file_id, scenario_name, represented_concepts[] |
| `SeedDataBusiness` | file_id, table_id, encodes_canonical_business_data (true/false) |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `is_authoritative_for` | BusinessConfigFile → Entitlement/PricingPlan/Geography | source-of-truth assertion |
| `derived_from_config` | Entitlement/Plan node → BusinessConfigFile | extraction provenance |
| `referenced_by_code` | BusinessConfigFile → Function/Class | who reads it |

### Extraction strategy

1. Heuristic file classification: `pricing*.json`, `plans.yaml`, `entitlements.json`, `flags.json`, `regions.json`, `i18n/*.json`.
2. Parse, validate against any embedded `$schema`.
3. Cross-link parsed contents with corresponding business nodes.
4. Mark divergence between config and business node as drift.

### Drift signal
- `config_authority_conflict` — pricing in `pricing.json` differs from `PricingPlan.tiers` — which is canonical?
- `silent_config_change` — config file changed in a commit without matching ticket/PR description; likely a hotfix bypassing normal product flow.

---

## 10. Category 8 — Test Names as Living Specifications

Test descriptions are often the only place behavior is stated in plain language. They are also the most reliably current — they run.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `TestNameAsSpec` | test_case_id, description_text, parsed_form (`given/when/then` if Gherkin-like), implies_requirement_id_candidate |
| `ScenarioTest` | test_case_id, given, when, then, links_to_AcceptanceCriterion_candidate |
| `ContractTestSpec` | test_case_id, contract_endpoint_id, scenario, expected_response_summary |
| `BehaviorByExample` | test_case_id, inputs_text, outputs_text, exception_text? |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `specifies_behavior` | TestNameAsSpec → Function/Endpoint/Component | the test acts as living spec |
| `implies_acceptance_criterion` | ScenarioTest → AcceptanceCriterion | candidate, with confidence |
| `documents_business_rule` | TestNameAsSpec → BusinessRule | |

### Extraction strategy

1. Pull `describe`/`it`/`test`/`Scenario`/`Feature` strings.
2. Parse Gherkin-like patterns; LLM-classify when unstructured.
3. Pair with the function-under-test (via existing `tests` edge from v0.1).
4. When test name asserts a business rule that has no `BusinessRule` node, propose creation.

### Drift signal
- `unspecified_business_endpoint` — endpoint with `revenue_attributed_to` or `gated_by_entitlement` but no test whose name documents the business rule.
- `test_implies_undocumented_rule` — test asserts behavior beyond what any `AcceptanceCriterion`/`BusinessRule` records.

---

## 11. Category 9 — Database Schema as Business Encoding

The DB schema is often a more honest model of the business than the PRD.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `BusinessColumn` | column_id, business_classification (status/lifecycle/audit/tenancy/identity/financial/temporal/relationship/external_id) |
| `LifecycleColumn` | column_id, observed_states[] (from existing data + enum), candidate_lifecycle_stages[] |
| `AuditColumn` | column_id, kind (created_at/updated_at/created_by/updated_by/deleted_at/version) |
| `TenancyColumn` | column_id, tenancy_kind (workspace/account/region/team/user) |
| `ExternalIDColumn` | column_id, external_system_candidate (stripe/auth0/segment/...) |
| `MoneyColumn` | column_id, currency_column?, currency_assumption, scale (cents/whole), classification (price/discount/tax/fee/balance) |
| `EnumValuesObserved` | column_id, observed_values[], frequency_distribution{}, candidate_taxonomy |
| `PIIColumnInferred` | column_id, classification (email/name/phone/address/...), inferred_via (name_pattern/data_pattern/regex), confidence |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `models_business_lifecycle` | LifecycleColumn → BusinessProcess/JourneyStage | |
| `tenant_scoped_by` | Table → TenancyColumn | every multi-tenant query needs to filter by this |
| `tracks_business_event` | AuditColumn → AuditEventDefinition | |
| `embeds_money_field` | MoneyColumn → BillingEvent/RevenueRule | |
| `pii_inferred` | Column → DataClassification | inferred edge |

### Extraction strategy

1. Naming heuristics on column names (`*_at`, `*_id`, `is_*`, `has_*`, `status`, `state`, `*_email`, `*_currency`).
2. Type-based heuristics (`numeric(10,2)` + name contains `price/amount/balance` → money).
3. Distinct-value sampling on enum-like columns to infer taxonomies.
4. PII detection via column-name regex + (if permitted) data-pattern sampling.
5. Cross-reference with `business-context-v0.2.md` business nodes.

### Drift signals
- `untenanted_table` — multi-tenant app with table missing a `TenancyColumn` → cross-tenant leakage risk.
- `missing_audit_columns` — financial table without `created_at`/`created_by` → compliance risk.
- `pii_unannotated_column` — high-confidence PII column not linked to `DataClassification` or `PIIField`.
- `lifecycle_state_undocumented` — `LifecycleColumn` with observed states not mapped to any `LifecycleStage`.
- `money_no_currency` — `MoneyColumn` without an explicit currency column or assumption → multi-currency bug waiting.

---

## 12. Category 10 — Commit, Branch & PR Metadata

The richest source of *recent* business intent.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `CommitBusinessSignal` | commit_sha, conventional_type (feat/fix/chore/...), scope, body, references[] (tickets/ADRs), revert_of? |
| `BranchInitiative` | branch_name, parsed_initiative (e.g., `feature/q4-pricing` → "q4-pricing"), candidate_epic_id |
| `PRBusinessRationale` | pr_id, body_sections{ what, why, risk, rollout, screenshots }, references[] |
| `RevertChain` | original_commit, revert_commit, reason_text, days_in_production |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `commit_advances` | Commit → Ticket/Feature | from references |
| `branch_pursues` | Branch → Epic/Initiative | inferred |
| `pr_explains` | PullRequest → BusinessRationale | extracted |
| `reverted_by` | Commit → Commit | with `RevertChain` |

### Extraction strategy

1. Parse conventional commits (`feat(billing): ...`).
2. Extract ticket references (`LIN-1234`, `#456`, `Refs: ABC-789`).
3. Naming convention detection on branches.
4. Section-parse PR descriptions (most teams have implicit templates; LLM-extract).
5. Trace reverts.

### Drift signals
- `commit_without_business_link` — feat/fix commit with no ticket reference → invisible work.
- `revert_without_postmortem` — production revert with no postmortem link → systemic learning lost.
- `branch_orphan` — long-lived branch with no `branch_pursues` target → stale work.

---

## 13. Category 11 — Module/Folder Organization as Business Architecture

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `ModuleBusinessGrouping` | module_id, candidate_capability_id, naming_convention, sibling_modules[] |
| `BoundedContextCandidate` | folder_path, members[], cohesion_score, coupling_score, suggested_name |
| `LayerAssignment` | module_id, candidate_layer (presentation/application/domain/infrastructure), evidence |
| `MonorepoPackageBusinessRole` | package_id, business_role_candidate (app/lib/sdk/contract/internal-tool) |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `groups_capability` | Module/Folder → Capability | |
| `bounded_context_for` | Folder → BoundedContextCandidate | |
| `layer_of` | Module → ArchitecturalLayer | |
| `cross_context_dependency` | Function → Function (across BoundedContextCandidates) | flagged for review |

### Extraction strategy

1. Walk folder structure; build candidate `BoundedContext` from cohesive subtrees.
2. Score cohesion/coupling using import graph.
3. Match folder names against `Capability` catalog.
4. Detect cross-context calls (a sign of either intentional integration or boundary erosion).

### Drift signal
- `boundary_erosion` — module imports breaching its bounded context → architectural drift candidate.
- `unowned_module` — module with no `groups_capability` and no team `owned_by` → bus-factor risk.

---

## 14. Category 12 — Error Codes & Status Conventions

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `BusinessErrorCode` | code, message_template, http_status, surface (user/api/internal), classification (auth/payment/quota/legal/validation/state/integration), candidate_business_outcome |
| `HTTPStatusUsagePattern` | endpoint_id, status_code, intended_meaning_text, frequency |
| `ErrorTaxonomyTree` | root_code, children[], hierarchy_depth, completeness_score |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `signals_business_outcome` | BusinessErrorCode → LifecycleStage/Commitment/Policy | |
| `endpoint_returns_error` | Endpoint → BusinessErrorCode | |
| `documented_in_api_doc` | BusinessErrorCode → APIDocPage | |

### Extraction strategy

1. Inventory all error class instantiations + raised codes.
2. Cluster by code prefix (`PAYMENT_*`, `QUOTA_*`).
3. Cross-reference with public API docs and SDK error catalogs.

### Drift signal
- `undocumented_error_in_response` — error returned by endpoint but not in `ContractEndpoint` error schema → contract gap.
- `inconsistent_status_use` — same business outcome returned with different HTTP statuses across endpoints.

---

## 15. Category 13 — Implicit Invariants & Runtime Guards

The unwritten rules that the code enforces but no document describes.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `RuntimeGuard` | location, predicate_text, on_failure (throw/return/log), referenced_business_concept_candidate |
| `ImplicitInvariant` | scope_id, statement (extracted from guard text + comment), evidence_locations[] |
| `PreconditionAssertion` | function_id, predicate_text, assertion_kind (param_validation/state_check/permission) |
| `PostconditionAssertion` | function_id, predicate_text, evidence_kind (return_check/audit_log) |
| `DataIntegrityCheck` | location, check_text, candidate_BusinessRule_link |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `enforces_invariant` | RuntimeGuard → Invariant | |
| `enforces_business_rule_implicitly` | RuntimeGuard → BusinessRule | when no annotation present |
| `precondition_of` | PreconditionAssertion → Function | |

### Extraction strategy

1. Detect `assert*`, `invariant`, `require`, `throw new ...` at function entry / state transitions.
2. Extract predicate text; LLM-classify into business statement.
3. Compare against `Invariant` catalog; surface missing entries as candidate invariants.

### Drift signal
- `invariant_only_in_code` — runtime guard enforcing a business rule with no documented `Invariant` → tribal knowledge.
- `documented_invariant_unenforced` — `Invariant` node with no `enforces_invariant` from code or DB constraint → on the honor system.

---

## 16. The Bridge Edges — Consolidated Cross-Reference

Every business-in-code node ultimately links to a business node. Here is the master bridge table:

| In-code element | Bridges via | To business node |
|-----------------|-------------|------------------|
| Identifier token | `references_domain_term` | `GlossaryTerm` |
| Type/class | `embodies_concept` | `DomainConcept` |
| Folder/module | `groups_capability` | `Capability` |
| Numeric constant | `embeds_price` / `embeds_quota` / `embeds_window` | `PricingTier` / `QuotaDefinition` / `Trial`/`RefundPolicy` |
| Plan-name string literal | `references_plan_literal` | `PricingPlan` |
| Entitlement-key string | `references_entitlement_literal` | `Entitlement` |
| Event-name string | `references_event_literal` | `AnalyticsEvent` |
| Error message | `voice_compliant_in_code` / `voice_violates_in_code` | `BrandVoiceGuideline` |
| `@RequiresEntitlement` | `gated_by_entitlement` (asserted) | `Entitlement` |
| `@PII` | `handles_pii` (asserted) | `PIIField` |
| `@Audit` | `audited_by` (asserted) | `AuditEventDefinition` |
| Conditional on plan | `gates_for_plan` | `PricingPlan` |
| Conditional on region | `gates_for_geography` | `Geography` |
| Conditional on lifecycle | `gates_for_lifecycle_stage` | `LifecycleStage` |
| Docstring referencing ticket | `references_ticket_in_code` | `Ticket` |
| Comment with ADR link | `references_adr_in_code` | `ADR` |
| `pricing.json` | `is_authoritative_for` | `PricingPlan`/`Entitlement` |
| Test name | `specifies_behavior` / `implies_acceptance_criterion` | `Function`/`AcceptanceCriterion` |
| `LifecycleColumn` | `models_business_lifecycle` | `BusinessProcess`/`JourneyStage` |
| `MoneyColumn` | `embeds_money_field` | `BillingEvent`/`RevenueRule` |
| Commit message | `commit_advances` | `Ticket`/`Feature` |
| Branch name | `branch_pursues` | `Epic`/`Initiative` |
| Error code | `signals_business_outcome` | `LifecycleStage`/`Commitment`/`Policy` |
| Runtime guard | `enforces_invariant` / `enforces_business_rule_implicitly` | `Invariant`/`BusinessRule` |

---

## 17. Drift Detection — The Bidirectional Loop

The bridge is what makes drift detection possible **in both directions**:

**Code → Business drift** (code changed, business model didn't):
- Pricing constant changed but `PricingTier` not updated.
- New `BusinessConditional` gating on a plan name not in `PricingPlan` catalog.
- New runtime guard enforcing an `Invariant` not yet documented.
- Endpoint returns an error code not present in `ContractEndpoint`.

**Business → Code drift** (business model changed, code didn't):
- `PricingTier.price_per_unit` changed but `PricingConstant` in code unchanged.
- `Entitlement` deprecated but `EntitlementKey` literal still appears in code.
- `Feature.unavailable_in: EU` declared but no `RegionalBranch` enforcing it.
- `BrandVoiceGuideline` updated but error messages still violate it.

Both directions become **first-class events** in the change feed (`ChangeEvent` from v0.1 §11), surfacing the rot you couldn't otherwise see.

---

## 18. Worked Example — Extracting Business Context from `createSubscription`

Continuing the running example. Same code from v0.1 trace, now seen through the bridge layer:

```
File: src/billing/handlers/createSubscription.ts

Source code (excerpted):
  const TRIAL_DAYS = 14;                                  // ← BusinessConstant / TimeConstant
  const ALLOWED_CADENCES = [7, 14, 30];                   // ← BusinessConstant / ThresholdConstant
  const PRO_PRICE_USD_CENTS = 2900;                       // ← PricingConstant

  @RequiresEntitlement("recurring_orders")                // ← EntitlementAnnotation
  @Audit(event: "subscription.created")                   // ← AuditAnnotation
  export async function createSubscription(req, res) {
    // FIXES LIN-4821: customers couldn't schedule monthly orders   ← TODOWithContext / CommentTicketReference
    if (req.user.plan === "free") {                       // ← BusinessConditional / gates_for_plan
      throw new InsufficientPlanError(                    // ← BusinessErrorCode (PAYMENT_REQUIRED, 402)
        "Recurring orders require a Pro plan or higher."  // ← ErrorMessageTemplate (voice check)
      );
    }
    if (req.user.region === "EU" && !req.user.dpa_signed) {  // ← BusinessConditional / RegionalBranch
      throw new ComplianceError("DPA_REQUIRED", 451);     // ← BusinessErrorCode (legal)
    }
    if (req.body.cadence_days !== undefined && 
        !ALLOWED_CADENCES.includes(req.body.cadence_days)) {
      throw new ValidationError("INVALID_CADENCE");       // ← RuntimeGuard / PreconditionAssertion
    }
    // ... persistence + external call (covered in v0.1 + code-context-v0.2) ...
  }

Generated bridge graph:

BusinessConstant(TRIAL_DAYS=14)
  ─embeds_window→ Trial(pro_extended) {length_days: 14}        ✓ matches business node
  
BusinessConstant(PRO_PRICE_USD_CENTS=2900)
  ─embeds_price→ PricingTier(plan: Pro, monthly_usd: 29.00)    ✓ matches
  
BusinessConstant(ALLOWED_CADENCES=[7,14,30])
  ─encodes_business_rule→ BusinessRule(REC-01: cadence ∈ {7,14,30})  ✓ matches v0.1 example

EntitlementAnnotation("recurring_orders") 
  ─gated_by_entitlement (asserted)→ Entitlement(recurring_orders)
                                    ✓ key exists in business catalog
                                    
AuditAnnotation("subscription.created")
  ─audited_by (asserted)→ AuditEventDefinition(subscription.created)
                          ✓ matches AnalyticsEvent / AuditLog catalog

CommentTicketReference("LIN-4821") 
  ─references_ticket_in_code→ Ticket(LIN-4821, status=Done)   ✓ alive
  
BusinessConditional(`req.user.plan === "free"`) 
  ─gates_for_plan→ PricingPlan(Free)
  ─enforces_implicitly→ BusinessRule(recurring_requires_paid_plan)
                                                              ⚠ no formal BusinessRule exists →
                                                              candidate creation surfaced

RegionalBranch(`req.user.region === "EU"`)
  ─gates_for_geography→ Geography(EU)
  ─enforces_implicitly→ JurisdictionalRule(GDPR-art-28: DPA required)
                                                              ✓ matches business-context-v0.2 example

BusinessErrorCode("PAYMENT_REQUIRED", 402)
  ─signals_business_outcome→ LifecycleStage(needs_upgrade)
  ─documented_in_api_doc→ APIDocPage("Errors")               ⚠ NOT documented → drift signal

BusinessErrorCode("DPA_REQUIRED", 451)
  ─signals_business_outcome→ ComplianceRequirement(GDPR-art-28)
  ─documented_in_api_doc→ ?                                   ⚠ NOT documented → drift signal

ErrorMessageTemplate("Recurring orders require a Pro plan or higher.")
  ─voice_compliant_in_code? → BrandVoiceGuideline("calm, brief, no blame")
                              ✓ matches (calm, brief, no blame)
  ─references_plan_literal→ PricingPlan(Pro)                  ✓ resolves

RuntimeGuard(`!ALLOWED_CADENCES.includes(...)`)
  ─enforces_invariant→ Invariant(REC-01)                      ✓ matches DB CheckConstraint enforcement

# Drift signals raised by this single function:
# 1. Two BusinessErrorCodes (402 PAYMENT_REQUIRED, 451 DPA_REQUIRED) returned but not in
#    ContractEndpoint(POST /subscriptions).response_schemas → contract documentation gap.
# 2. BusinessConditional(plan === "free") implies BusinessRule that doesn't exist as a node →
#    propose creation: BusinessRule("recurring_requires_paid_plan") with code as evidence.
# 3. Comment "FIXES LIN-4821" references a Done ticket → comment is fine but worth refreshing
#    if behavior changes.

# Things the bridge confirmed (no drift):
#  - Trial length matches.
#  - Pro price matches.
#  - Cadence rule matches DB constraint and ADR-0042.
#  - Entitlement key exists.
#  - Audit event registered.
#  - Brand voice clean.
#  - Geographic gate aligned with declared JurisdictionalRule.
```

The bridge layer transforms a single file from "source code" into a queryable assertion of *what the business is and where it differs from what the business says it is*.

---

## 19. Lossless Concerns Specific to the Bridge Layer

| Inferred fact | How we keep it lossless | Watch out for |
|---------------|------------------------|---------------|
| Identifier → glossary match | Tokens stored verbatim; match path retained; multiple candidates kept | Synonym overreach — set conservative confidence threshold |
| Magic number → business meaning | Literal value + surrounding context lines stored | False positives; humans must confirm before authoritative status |
| Conditional → business gate | Predicate text verbatim; classifier output with rule version | Predicates evolve — re-classify on every change |
| Annotation → asserted edge | Annotation source location + args verbatim | Asserted edges trump inferred but disagreement is recorded |
| Test name → behavior spec | Description text verbatim; LLM-parsed Gherkin form coexists with raw | Description rewrites should preserve historical text |
| DB column → business field | Column DDL + name + comment verbatim; observed enum values + frequency | Sampling of values may need access governance |
| Commit message → business signal | Full message body retained; parsed structure additive | Squashed merges lose history — also retain individual commits |
| Folder → bounded context | Cohesion/coupling scores tagged with rule version | Refactors invalidate; recompute on rename |
| Runtime guard → invariant | Predicate verbatim + on-failure behavior; LLM rewrite separate | Don't paraphrase guards into invariants — keep both |

---

## 20. Open Questions for v0.3

1. **Confidence calibration.** Inferred bridge edges accumulate. Need a system-wide policy for when an inferred edge gets "promoted" to asserted (manual review? threshold? quorum of evidence?).
2. **Cross-language identifier matching.** A `Subscription` class in Python and a `Subscription` interface in TypeScript should both `embodies_concept→DomainConcept(Subscription)`. Identity resolution across languages is non-trivial.
3. **NLP infrastructure.** Tokenization, lemmatization, and concept matching need a per-codebase model. Bootstrapping (terms inferred from code itself become candidate `GlossaryTerm`s) creates a positive feedback loop — design it carefully.
4. **Annotation catalog curation.** Each company has its own annotation vocabulary. Need a `BusinessAnnotationCatalog` definition mechanism (probably plugin-style).
5. **Sampling DB data for inference.** Useful but sensitive. Permissioning and PII handling for sampling needs to be first-class, not an afterthought.
6. **Stable inference identity.** When the inference rule version changes and re-extraction produces different candidates, how do we preserve historical conclusions and the reasoning chain that led to them?
7. **Bridge edges as feedback for the business catalog.** Every `unbound_constant`, `unmapped_business_literal`, `gate_for_unknown_plan` is a *suggestion* to extend the business catalog. Build a curation queue and review workflow as a first-class product surface.
8. **Cost discipline.** LLM-classifying every constant/conditional/string is expensive. Tier the extractor: regex-first, then small-model, then large-model only for low-confidence remainders.
