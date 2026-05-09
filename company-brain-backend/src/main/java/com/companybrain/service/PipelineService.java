package com.companybrain.service;

import com.companybrain.dto.PipelineResultRequest;
import com.companybrain.dto.PipelineStartRequest;
import com.companybrain.model.Artifact;
import com.companybrain.model.PipelineJob;
import com.companybrain.repository.NodeRepository;
import com.companybrain.repository.PipelineJobRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.reactive.function.client.WebClient;

import java.nio.charset.StandardCharsets;
import java.sql.Array;
import java.sql.Connection;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Owns the full pipeline lifecycle.
 *
 * Responsibilities:
 *  1. createJob()      — persist a PipelineJob(status=running), return job_id to frontend
 *  2. dispatchToAi()   — async: POST to AI service at /pipeline/run with request + callback URL
 *  3. applyResult()    — called by /v1/internal/pipeline-result when AI finishes:
 *                        writes entities/edges/contexts into the graph DB, then marks job done.
 *  4. updateProgress() — called mid-run to push live logs to the job record
 *
 * Performance: applyResult() uses JdbcTemplate.batchUpdate() throughout so that a typical
 * pipeline result (50 entities + 100 edges + 50 contexts) is written in ~5 SQL statements
 * rather than ~200.  The old per-entity save() loop has been replaced with:
 *   Phase 1 — batch node upsert via INSERT … ON CONFLICT DO UPDATE
 *   Phase 2 — batch edge upsert via INSERT … ON CONFLICT DO UPDATE
 *   Phase 3 — batch context replace via DELETE + INSERT
 *   Phase 4 — batch intent-context metadata merge via UPDATE
 *   Phase 5 — batch artifact link replace via DELETE + INSERT
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class PipelineService {

    private final PipelineJobRepository  jobRepository;
    private final NodeRepository         nodeRepository;
    private final ArtifactWriterService  artifactWriterService;
    private final JdbcTemplate           jdbc;
    private final ObjectMapper           objectMapper;

    @Value("${app.ai-service.url:http://localhost:8000}")
    private String aiServiceUrl;

    @Value("${app.internal-api-key:dev-internal-key}")
    private String internalApiKey;

    private final WebClient webClient = WebClient.builder().build();

    // ── 1. Create job ─────────────────────────────────────────────────────────

    @Transactional
    public PipelineJob createJob(UUID workspaceId, PipelineStartRequest request) {
        PipelineJob job = PipelineJob.builder()
                .workspaceId(workspaceId)
                .endpointPath(request.getEndpointPath())
                .httpMethod(request.getHttpMethod())
                .status("running")
                .startedAt(OffsetDateTime.now())
                .progressLogs(new ArrayList<>())
                .build();
        job = jobRepository.save(job);
        log.info("[pipeline] Job created  jobId={}  endpoint={}  workspace={}",
                job.getId(), job.getEndpointPath(), workspaceId);
        return job;
    }

    // ── 2. Dispatch to AI service ─────────────────────────────────────────────

    @Async
    public void dispatchToAi(UUID jobId, UUID workspaceId, PipelineStartRequest request) {
        log.info("[pipeline] Dispatching to AI service  jobId={}", jobId);

        Map<String, Object> payload = new HashMap<>();
        payload.put("job_id",        jobId.toString());
        payload.put("workspace_id",  workspaceId.toString());
        payload.put("endpoint_path", request.getEndpointPath());
        payload.put("http_method",   request.getHttpMethod());
        payload.put("branch",        request.getBranch());
        payload.put("callback_url",  "http://localhost:8080/v1/internal/pipeline-result");
        payload.put("callback_key",  internalApiKey);

        List<Map<String, Object>> repos = new ArrayList<>();
        if (request.getRepos() != null) {
            for (var repo : request.getRepos()) {
                Map<String, Object> r = new HashMap<>();
                if (repo.getLocalPath() != null) r.put("local_path", repo.getLocalPath());
                if (repo.getUrl()       != null) r.put("url",        repo.getUrl());
                r.put("type",   repo.getType());
                r.put("branch", repo.getBranch() != null ? repo.getBranch() : request.getBranch());
                repos.add(r);
            }
        }
        payload.put("repos", repos);

        try {
            webClient.post()
                    .uri(aiServiceUrl + "/pipeline/run")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(payload)
                    .retrieve()
                    .toBodilessEntity()
                    .block();
            log.info("[pipeline] AI service accepted  jobId={}", jobId);
        } catch (Exception e) {
            log.error("[pipeline] Failed to dispatch  jobId={}  error={}", jobId, e.getMessage());
            markFailed(jobId, workspaceId, "AI service unreachable: " + e.getMessage(),
                    Collections.emptyList());
        }
    }

    // ── 3. Apply result ───────────────────────────────────────────────────────

    /**
     * Writes all extracted entities, relationships, and contexts to the graph DB.
     * Uses batch SQL throughout — O(1) round-trips per phase instead of O(N).
     */
    @Transactional
    public void applyResult(PipelineResultRequest result) {
        UUID jobId       = result.getJobId();
        UUID workspaceId = result.getWorkspaceId();

        log.info("[pipeline] Applying result  jobId={}  status={}  entities={}  edges={}",
                jobId, result.getStatus(),
                result.getEntities()      != null ? result.getEntities().size()      : 0,
                result.getRelationships() != null ? result.getRelationships().size() : 0);

        // Internal endpoints carry no JWT, so RlsInterceptor never sets app.workspace_id
        // for this connection. Without it, the FORCE'd RLS policy on `nodes` filters
        // every row from SELECT — Phase 1's pre-load returns empty, fresh UUIDs are
        // generated, the ON CONFLICT path keeps the existing row id, and Phase 5's
        // artifact_links insert then references node UUIDs that don't exist in DB.
        // Bind the session variable for this transaction so SELECTs see the workspace.
        setWorkspaceForRls(workspaceId);

        if ("failed".equals(result.getStatus())) {
            markFailed(jobId, workspaceId, result.getErrorMessage(),
                    result.getProgressLogs() != null ? result.getProgressLogs()
                                                     : Collections.emptyList());
            return;
        }

        // ── Phase 1: Batch node upsert ────────────────────────────────────────
        // nodeIds has TWO kinds of keys:
        //   "NodeType:extId"  → UUID for dedup within this batch (avoids PK collision
        //                       when two entities share the same file/name but differ by type,
        //                       e.g. ApiEndpoint:getPayerCompetitors vs Function:getPayerCompetitors)
        //   "extId"           → UUID (first-wins) used by edge resolution so edges can
        //                       reference a node by external_id without knowing its type.
        Map<String, UUID> nodeIds = new HashMap<>();    // typeKey OR extId → DB UUID
        int entityCount = 0;

        if (result.getEntities() != null && !result.getEntities().isEmpty()) {
            List<String> extIds = result.getEntities().stream()
                    .map(dto -> dto.getRepo() + "/" + dto.getFile() + "::" + dto.getName())
                    .distinct().toList();

            // 1 SELECT for all external IDs — pre-load existing UUIDs keyed both ways
            nodeRepository.findByWorkspaceIdAndExternalIdIn(workspaceId, extIds)
                    .forEach(n -> {
                        String typeKey = n.getNodeType() + ":" + n.getExternalId();
                        nodeIds.put(typeKey, n.getId());
                        nodeIds.putIfAbsent(n.getExternalId(), n.getId()); // plain key for edges
                    });

            // Assign UUIDs and deduplicate on (entityType, extId) within this batch.
            // Prevents duplicate primary keys when the same name appears under multiple types.
            Set<String>    seenTypeKeys = new LinkedHashSet<>();
            List<Object[]> rows         = new ArrayList<>(result.getEntities().size());

            for (var dto : result.getEntities()) {
                String extId   = dto.getRepo() + "/" + dto.getFile() + "::" + dto.getName();
                String typeKey = dto.getEntityType() + ":" + extId;

                if (!seenTypeKeys.add(typeKey)) continue; // duplicate within batch — skip

                // Reuse existing UUID or generate a new one, keyed by (type, extId)
                UUID id = nodeIds.computeIfAbsent(typeKey, k -> UUID.randomUUID());
                // Also register under plain extId so edge resolution works (first-wins)
                nodeIds.putIfAbsent(extId, id);

                Map<String, Object> meta = new LinkedHashMap<>();
                meta.put("file",                  dto.getFile());
                meta.put("repo",                  dto.getRepo());
                meta.put("signature",             dto.getSignature());
                meta.put("confidence",            dto.getConfidence());
                meta.put("first_appeared_commit", dto.getFirstAppearedCommit());
                meta.put("last_modified_commit",  dto.getLastModifiedCommit());
                // ADR-0040 Tier 1.A/B/2.A — content fields. Only persist when non-empty
                // so we don't bloat the JSONB with null keys.
                if (dto.getQueryText()             != null && !dto.getQueryText().isBlank()) {
                    meta.put("query_text",            dto.getQueryText());
                }
                if (dto.getCodeSnippet()           != null && !dto.getCodeSnippet().isBlank()) {
                    meta.put("code_snippet",          dto.getCodeSnippet());
                }
                if (dto.getJavadoc()               != null && !dto.getJavadoc().isBlank()) {
                    meta.put("javadoc",               dto.getJavadoc());
                }
                if (dto.getValidationConstraints() != null && !dto.getValidationConstraints().isEmpty()) {
                    meta.put("validation_constraints", dto.getValidationConstraints());
                }

                rows.add(new Object[]{
                        id.toString(),
                        workspaceId.toString(),
                        dto.getEntityType(),
                        toEntityType(dto.getEntityType()),
                        extId,
                        dto.getName(),
                        toJson(meta),
                        dto.getUrn()    // canonical URN per ADR-0013; may be null during transition
                });
            }

            // Batch INSERT split by URN presence (ADR-0013 transition):
            //
            // Rows WITH a URN use ON CONFLICT (workspace_id, urn) — URN is the canonical
            // identity per ADR-0013. Without this split, rows whose external_id changed
            // between runs (e.g. path format change) would skip the (workspace, type, extId)
            // conflict and crash on uq_nodes_urn.
            //
            // Rows WITHOUT a URN keep the legacy (workspace_id, node_type, external_id) target
            // so old AI service versions still work during the rollout window.
            List<Object[]> rowsWithUrn    = rows.stream().filter(r -> r[7] != null).toList();
            List<Object[]> rowsWithoutUrn = rows.stream().filter(r -> r[7] == null).toList();

            if (!rowsWithUrn.isEmpty()) {
                jdbc.batchUpdate("""
                        INSERT INTO nodes (id, workspace_id, node_type, entity_type, external_id, name, metadata, urn)
                        VALUES (?::uuid, ?::uuid, ?, ?, ?, ?, ?::jsonb, ?)
                        ON CONFLICT (workspace_id, urn)
                        DO UPDATE SET
                            node_type   = EXCLUDED.node_type,
                            entity_type = EXCLUDED.entity_type,
                            external_id = EXCLUDED.external_id,
                            name        = EXCLUDED.name,
                            metadata    = EXCLUDED.metadata
                        """, rowsWithUrn);
            }

            if (!rowsWithoutUrn.isEmpty()) {
                jdbc.batchUpdate("""
                        INSERT INTO nodes (id, workspace_id, node_type, entity_type, external_id, name, metadata, urn)
                        VALUES (?::uuid, ?::uuid, ?, ?, ?, ?, ?::jsonb, ?)
                        ON CONFLICT (workspace_id, node_type, external_id)
                        DO UPDATE SET
                            name        = EXCLUDED.name,
                            metadata    = EXCLUDED.metadata,
                            entity_type = EXCLUDED.entity_type,
                            urn         = COALESCE(EXCLUDED.urn, nodes.urn)
                        """, rowsWithoutUrn);
            }

            // Re-sync nodeIds after upsert — uses raw JDBC (NOT JpaRepository) for two reasons:
            //   1. ON CONFLICT (workspace_id, urn) within a single batch silently drops the
            //      second row's generated UUID and keeps the first row's. Any subsequent
            //      reference to the second entity would point at a phantom UUID and trigger
            //      a foreign-key violation in Phases 2–5 (edges/contexts/artifact_links).
            //   2. SET LOCAL app.workspace_id was issued via this same JdbcTemplate, so
            //      using it here guarantees the SELECT sees the workspace context. Going
            //      through JPA's EntityManager session is unreliable — depending on
            //      DataSource proxy / Hibernate Session connection acquisition, the
            //      SET LOCAL may or may not be visible there.
            //
            // Re-sync by URN AND by external_id so both conflict paths are covered:
            //   - rowsWithUrn:    URN-conflict can leave the second row with the FIRST row's
            //                     id but the SECOND row's external_id (we UPDATE external_id
            //                     in the conflict clause). Lookup by URN is canonical.
            //   - rowsWithoutUrn: legacy (workspace_id, node_type, external_id) path —
            //                     extId is canonical there.
            List<String> urnsForResync = rowsWithUrn.stream()
                    .map(r -> (String) r[7])
                    .filter(Objects::nonNull)
                    .distinct()
                    .toList();
            if (!urnsForResync.isEmpty()) {
                jdbc.query(
                        "SELECT id, node_type, external_id, urn FROM nodes "
                      + "WHERE workspace_id = ?::uuid AND urn = ANY(?::text[])",
                        ps -> {
                            ps.setString(1, workspaceId.toString());
                            ps.setArray(2, ps.getConnection()
                                    .createArrayOf("text", urnsForResync.toArray()));
                        },
                        rs -> {
                            UUID dbId  = UUID.fromString(rs.getString("id"));
                            String nt  = rs.getString("node_type");
                            String eid = rs.getString("external_id");
                            nodeIds.put(eid, dbId);
                            nodeIds.put(nt + ":" + eid, dbId);
                        });
            }

            List<String> extIdsForResync = rowsWithoutUrn.stream()
                    .map(r -> (String) r[4])
                    .filter(Objects::nonNull)
                    .distinct()
                    .toList();
            if (!extIdsForResync.isEmpty()) {
                jdbc.query(
                        "SELECT id, node_type, external_id FROM nodes "
                      + "WHERE workspace_id = ?::uuid AND external_id = ANY(?::text[])",
                        ps -> {
                            ps.setString(1, workspaceId.toString());
                            ps.setArray(2, ps.getConnection()
                                    .createArrayOf("text", extIdsForResync.toArray()));
                        },
                        rs -> {
                            UUID dbId  = UUID.fromString(rs.getString("id"));
                            String nt  = rs.getString("node_type");
                            String eid = rs.getString("external_id");
                            nodeIds.put(eid, dbId);
                            nodeIds.put(nt + ":" + eid, dbId);
                        });
            }

            entityCount = rows.size();
            log.info("[pipeline] Upserted {} entities  jobId={}", entityCount, jobId);
        }

        // ── Phase 2: Batch edge upsert ────────────────────────────────────────
        // nodeIds map is now fully populated — resolve source/target in memory, zero DB reads.
        int edgeCount = 0;

        if (result.getRelationships() != null && !result.getRelationships().isEmpty()) {
            List<Object[]> rows = new ArrayList<>();

            for (var dto : result.getRelationships()) {
                UUID sourceId = resolveId(nodeIds, workspaceId, dto.getFromEntity());
                UUID targetId = resolveId(nodeIds, workspaceId, dto.getToEntity());
                if (sourceId == null || targetId == null) {
                    log.debug("[pipeline] Skipping edge — node not found  from={}  to={}",
                            dto.getFromEntity(), dto.getToEntity());
                    continue;
                }
                rows.add(new Object[]{
                        UUID.randomUUID().toString(),
                        workspaceId.toString(),
                        sourceId.toString(),
                        targetId.toString(),
                        dto.getEdgeType(),
                        dto.getConfidence() != null ? dto.getConfidence() : 0.7
                });
            }

            // 1 batch INSERT … ON CONFLICT DO UPDATE
            jdbc.batchUpdate("""
                    INSERT INTO edges
                        (id, workspace_id, source_id, target_id, edge_type,
                         confidence, is_pruned, last_seen, observed_source)
                    VALUES
                        (?::uuid, ?::uuid, ?::uuid, ?::uuid, ?,
                         ?, false, now(), 'llm_extraction')
                    ON CONFLICT (workspace_id, source_id, target_id, edge_type)
                    DO UPDATE SET
                        confidence = GREATEST(EXCLUDED.confidence, edges.confidence),
                        is_pruned  = false,
                        last_seen  = now()
                    """, rows);

            edgeCount = rows.size();
            log.info("[pipeline] Upserted {} edges  jobId={}", edgeCount, jobId);
        }

        // ── Phase 3: Batch context replace ───────────────────────────────────
        // Strategy: DELETE existing llm_synthesis contexts for affected nodes,
        // then bulk INSERT the new ones. Avoids needing a unique constraint.
        int contextCount = 0;

        if (result.getContexts() != null && !result.getContexts().isEmpty()) {
            List<Object[]> insertRows = new ArrayList<>();
            List<String>   nodeUuids = new ArrayList<>();

            for (var dto : result.getContexts()) {
                UUID nodeId = resolveId(nodeIds, workspaceId, dto.getEntityExternalId());
                if (nodeId == null) continue;

                nodeUuids.add(nodeId.toString());

                String purpose = dto.getPurpose() != null ? dto.getPurpose() : "";
                String title   = purpose.length() > 255 ? purpose.substring(0, 255) : purpose;
                String body    = buildContextBody(dto);
                String conf    = changeRiskToConfidence(dto.getChangeRisk());

                insertRows.add(new Object[]{
                        UUID.randomUUID().toString(),
                        workspaceId.toString(),
                        nodeId.toString(),
                        "llm_synthesis",
                        title,
                        body.getBytes(StandardCharsets.UTF_8),
                        conf
                });
            }

            if (!nodeUuids.isEmpty()) {
                // 1 parameterized DELETE using PostgreSQL ANY(?) — no string concatenation
                jdbc.update(con -> {
                    var ps = con.prepareStatement(
                            "DELETE FROM node_context "
                            + "WHERE workspace_id = ?::uuid "
                            + "  AND context_type = 'llm_synthesis' "
                            + "  AND node_id = ANY(?::uuid[])");
                    ps.setString(1, workspaceId.toString());
                    ps.setArray(2, con.createArrayOf("uuid",
                            nodeUuids.stream().map(UUID::fromString).toArray()));
                    return ps;
                });

                // 1 batch INSERT
                jdbc.batchUpdate("""
                        INSERT INTO node_context
                            (id, workspace_id, node_id, context_type, title, body, confidence, occurred_at)
                        VALUES (?::uuid, ?::uuid, ?::uuid, ?, ?, ?, ?, now())
                        """, insertRows);

                contextCount = insertRows.size();
                log.info("[pipeline] Replaced {} contexts  jobId={}", contextCount, jobId);
            }
        }

        // ── Phase 4: Batch intent-context metadata merge ──────────────────────
        // Stage 1.5 (IntentSynthesizer) enriches each function node with business
        // intent fields.  We merge these into the node's JSONB metadata column.
        // All nodes are already in nodeIds (populated in Phase 1 or via the freshness
        // cache), so no additional SELECTs are needed.
        if (result.getIntentContexts() != null && !result.getIntentContexts().isEmpty()) {
            List<Object[]> rows = new ArrayList<>();

            // Fetch the nodes we need to merge into (those not seen in Phase 1)
            Set<String> missingExtIds = result.getIntentContexts().keySet().stream()
                    .filter(eid -> !nodeIds.containsKey(eid))
                    .collect(Collectors.toSet());
            if (!missingExtIds.isEmpty()) {
                nodeRepository.findByWorkspaceIdAndExternalIdIn(workspaceId, missingExtIds)
                        .forEach(n -> nodeIds.put(n.getExternalId(), n.getId()));
            }

            for (var entry : result.getIntentContexts().entrySet()) {
                UUID nodeId = resolveId(nodeIds, workspaceId, entry.getKey());
                if (nodeId == null) continue;

                Map<String, Object> functionContext = entry.getValue();

                // Build merged metadata: we append onto whatever the node already has.
                // Use a JSONB merge expression so we don't need to read first.
                Map<String, Object> patch = new LinkedHashMap<>();
                patch.put("functionContext", functionContext);
                if (functionContext.get("change_risk")   != null)
                    patch.put("changeRisk",       functionContext.get("change_risk"));
                if (functionContext.get("change_reason") != null)
                    patch.put("changeRiskReason", functionContext.get("change_reason"));
                if (functionContext.get("purpose")       != null)
                    patch.put("purpose",          functionContext.get("purpose"));

                rows.add(new Object[]{ toJson(patch), nodeId.toString() });
            }

            if (!rows.isEmpty()) {
                // JSONB concatenation operator (||) merges patch onto existing metadata
                jdbc.batchUpdate("""
                        UPDATE nodes
                        SET metadata = COALESCE(metadata, '{}'::jsonb) || ?::jsonb
                        WHERE id = ?::uuid
                        """, rows);
                log.info("[pipeline] Merged {} intent contexts  jobId={}", rows.size(), jobId);
            }
        }

        // ── Phase 5: Artifact upserts + batch link replace ────────────────────
        Map<String, UUID> artifactExtIdToDbId = new HashMap<>();

        if (result.getArtifacts() != null) {
            for (var dto : result.getArtifacts()) {
                if (dto.getExternalId() == null || dto.getKind() == null) continue;
                try {
                    Artifact artifact = artifactWriterService.upsertArtifact(
                            workspaceId,
                            dto.getKind(),
                            dto.getExternalId(),
                            dto.getContent()  != null ? dto.getContent() : "",
                            dto.getSourceUri(),
                            dto.getAuthor(),
                            dto.getMetadata()
                    );
                    artifactExtIdToDbId.put(dto.getExternalId(), artifact.getId());
                } catch (Exception e) {
                    log.warn("[pipeline] artifact upsert failed  kind={}  extId={}  err={}",
                            dto.getKind(), dto.getExternalId(), e.getMessage());
                }
            }
            log.info("[pipeline] Upserted {} artifacts  jobId={}", artifactExtIdToDbId.size(), jobId);
        }

        if (result.getArtifactLinks() != null && !result.getArtifactLinks().isEmpty()) {
            // Collect all link rows so we can batch-delete-then-insert
            List<Object[]>  insertRows = new ArrayList<>();
            Set<String>     affectedNodeIds = new LinkedHashSet<>();

            for (var entry : result.getArtifactLinks().entrySet()) {
                UUID nodeId = resolveId(nodeIds, workspaceId, entry.getKey());
                if (nodeId == null) continue;

                for (String artifactExtId : entry.getValue()) {
                    UUID artifactDbId = artifactExtIdToDbId.get(artifactExtId);
                    if (artifactDbId == null) continue;

                    affectedNodeIds.add(nodeId.toString());
                    insertRows.add(new Object[]{
                            artifactDbId.toString(),   // artifact_id  (PK part 1)
                            workspaceId.toString(),    // workspace_id
                            nodeId.toString(),         // node_id      (PK part 2)
                            "derived_from",            // link_role    (PK part 3)
                            "1.0"                      // confidence
                    });
                }
            }

            if (!insertRows.isEmpty()) {
                // ── SAFETY NET ─────────────────────────────────────────────────
                // Verify every node_id we're about to FK-reference actually exists in DB.
                // Even with the URN re-sync above, if any code path leaves an orphan UUID
                // in nodeIds we drop those rows with a loud log instead of crashing the
                // whole transaction (which leaves the job stuck in "running" forever).
                final Set<String> nodeIdsToCheck = Set.copyOf(affectedNodeIds);
                Set<String> orphanNodeIds = new HashSet<>(nodeIdsToCheck);
                jdbc.query(
                        "SELECT id::text FROM nodes "
                      + "WHERE workspace_id = ?::uuid AND id = ANY(?::uuid[])",
                        ps -> {
                            ps.setString(1, workspaceId.toString());
                            ps.setArray(2, ps.getConnection()
                                    .createArrayOf("uuid", nodeIdsToCheck.toArray()));
                        },
                        rs -> { orphanNodeIds.remove(rs.getString(1)); });

                if (!orphanNodeIds.isEmpty()) {
                    log.error("[pipeline] ⚠ {} orphan node_id(s) in artifact_links cache  jobId={}  nodeIds={}",
                            orphanNodeIds.size(), jobId, orphanNodeIds);
                    // Reverse-lookup which entity externalIds these came from so we can fix the root cause
                    nodeIds.entrySet().stream()
                            .filter(e -> orphanNodeIds.contains(e.getValue().toString()))
                            .forEach(e -> log.error("[pipeline]   orphan: nodeIds[{}] = {}", e.getKey(), e.getValue()));
                    insertRows = insertRows.stream()
                            .filter(r -> !orphanNodeIds.contains((String) r[2]))
                            .collect(Collectors.toList());
                    affectedNodeIds = affectedNodeIds.stream()
                            .filter(id -> !orphanNodeIds.contains(id))
                            .collect(Collectors.toCollection(LinkedHashSet::new));
                }

                if (insertRows.isEmpty()) {
                    log.warn("[pipeline] All artifact_link rows filtered as orphans — skipping insert  jobId={}", jobId);
                } else {
                    // 1 parameterized DELETE using PostgreSQL ANY(?) — no string concatenation
                    final Set<String> affected = affectedNodeIds;
                    jdbc.update(con -> {
                        var ps = con.prepareStatement(
                                "DELETE FROM artifact_links "
                                + "WHERE workspace_id = ?::uuid "
                                + "  AND link_role = 'derived_from' "
                                + "  AND node_id = ANY(?::uuid[])");
                        ps.setString(1, workspaceId.toString());
                        ps.setArray(2, con.createArrayOf("uuid",
                                affected.stream().map(UUID::fromString).toArray()));
                        return ps;
                    });

                    // 1 batch INSERT — artifact_links PK is (artifact_id, node_id, link_role),
                    // there is no synthetic id column (see V3__create_artifact_tables.sql)
                    jdbc.batchUpdate("""
                            INSERT INTO artifact_links
                                (artifact_id, workspace_id, node_id, link_role, confidence)
                            VALUES (?::uuid, ?::uuid, ?::uuid, ?, ?::numeric)
                            ON CONFLICT DO NOTHING
                            """, insertRows);

                    log.info("[pipeline] Wrote {} artifact links  jobId={}", insertRows.size(), jobId);
                }
            }
        }

        // ── Mark job completed — single UPDATE, no SELECT round-trip ─────────
        try {
            // stages_summary and progress_logs are jsonb arrays — fall back to "[]" not "{}"
            String stagesSummaryJson = result.getStagesSummary() != null
                    ? objectMapper.writeValueAsString(result.getStagesSummary())
                    : "[]";
            String progressLogsJson = result.getProgressLogs() != null
                    ? objectMapper.writeValueAsString(result.getProgressLogs())
                    : "[]";
            // files_traced is a jsonb array column — must pass a JSON string, not an integer count
            String filesTracedJson = result.getFilesTraced() != null
                    ? objectMapper.writeValueAsString(result.getFilesTraced())
                    : "[]";

            jobRepository.markCompleted(
                    jobId,
                    entityCount,
                    edgeCount,
                    /* gapCount */ 0,
                    result.getCodeUnitsFound(),
                    result.getGitCommitsFound(),
                    filesTracedJson,
                    stagesSummaryJson,
                    progressLogsJson
            );
        } catch (JsonProcessingException e) {
            log.warn("[pipeline] Failed to serialize completion metadata  jobId={}  error={}", jobId, e.getMessage());
            // Fallback: still mark completed without the JSONB fields
            jobRepository.markCompleted(jobId, entityCount, edgeCount, 0, null, null, "[]", "[]", "[]");
        }

        log.info("[pipeline] ✅ Job complete  jobId={}  entities={}  edges={}  contexts={}",
                jobId, entityCount, edgeCount, contextCount);
    }

    // ── 4. Progress update (mid-run) ──────────────────────────────────────────

    @Transactional
    public void updateProgress(UUID jobId, List<Object> logs) {
        // Direct UPDATE — no SELECT needed; avoids the fetch-then-save anti-pattern
        // that was firing one SELECT per progress push (visible as repeated pipeline_jobs queries).
        try {
            String logsJson = objectMapper.writeValueAsString(logs);
            jobRepository.updateProgressLogs(jobId, logsJson);
        } catch (JsonProcessingException e) {
            log.warn("[pipeline] Failed to serialize progress logs  jobId={}  error={}", jobId, e.getMessage());
        }
    }

    // ── Internal helpers ──────────────────────────────────────────────────────

    @Transactional
    public void markFailed(UUID jobId, UUID workspaceId, String error, List<Object> logs) {
        // Direct UPDATE — no SELECT round-trip (workspaceId used only for logging)
        try {
            String logsJson = objectMapper.writeValueAsString(logs != null ? logs : Collections.emptyList());
            jobRepository.markFailed(jobId, error != null ? error : "Unknown error", logsJson);
        } catch (JsonProcessingException e) {
            log.warn("[pipeline] Failed to serialize failure logs  jobId={}  error={}", jobId, e.getMessage());
            jobRepository.markFailed(jobId, error != null ? error : "Unknown error", "[]");
        }
        log.warn("[pipeline] Job marked failed  jobId={}  workspace={}  error={}", jobId, workspaceId, error);
    }

    /**
     * Resolve an externalId to its DB UUID.
     * Checks the in-memory map first (covers all nodes written in Phase 1);
     * falls back to a DB lookup for nodes that were already in the graph from
     * a prior pipeline run and weren't included in this result's entity list.
     */
    private UUID resolveId(Map<String, UUID> nodeIds, UUID workspaceId, String externalId) {
        if (externalId == null) return null;
        UUID cached = nodeIds.get(externalId);
        if (cached != null) return cached;
        return nodeRepository.findByWorkspaceIdAndExternalId(workspaceId, externalId)
                .map(n -> {
                    nodeIds.put(externalId, n.getId()); // cache it for next time
                    return n.getId();
                })
                .orElse(null);
    }

    /**
     * Serialize the business context body as proper JSON via ObjectMapper.
     * Replaces the old String.format() approach that was fragile with quotes/backslashes.
     */
    private String buildContextBody(PipelineResultRequest.ContextDto dto) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("purpose",         dto.getPurpose());
        body.put("history_summary", dto.getHistorySummary());
        body.put("invariants",      dto.getInvariants() != null ? dto.getInvariants() : List.of());
        body.put("change_risk",     dto.getChangeRisk());
        body.put("change_risk_reason", dto.getChangeRiskReason());
        body.put("owner_team",      dto.getOwnerTeam());
        body.put("external_dependencies",
                dto.getExternalDependencies() != null ? dto.getExternalDependencies() : List.of());
        body.put("gaps",            dto.getGaps() != null ? dto.getGaps() : List.of());
        return toJson(body);
    }

    private static String changeRiskToConfidence(String changeRisk) {
        if (changeRisk == null) return "medium";
        return switch (changeRisk.toUpperCase()) {
            case "LOW"  -> "high";
            case "HIGH" -> "low";
            default     -> "medium";
        };
    }

    /** Maps raw LLM node_type to the constrained entity_type taxonomy (V6 migration). */
    private static String toEntityType(String nodeType) {
        return switch (nodeType) {
            case "ApiEndpoint"                       -> "api_contract";
            case "SchemaField", "DatabaseTable",
                 "DatabaseColumn", "DatabaseQuery",
                 "SharedType"                        -> "data_model";
            case "FrontendComponent", "Screen"       -> "component";
            case "Assumption"                        -> "assumption";
            case "BusinessContext"                   -> "business_context";
            case "Function", "CodeFunction",
                 "Method"                            -> "function_node";
            default                                  -> "component";
        };
    }

    /**
     * Bind app.workspace_id for the current transaction so RLS policies on
     * nodes/edges/node_context/artifacts/etc. resolve correctly. Internal-key
     * endpoints (AI service callbacks) bypass JwtAuthFilter and the RlsInterceptor
     * never sees a workspace, so we must set it here from the request body.
     *
     * SET LOCAL is scoped to the active transaction and reverts on commit/rollback,
     * so the pooled connection is safe to reuse for the next request. The UUID
     * is interpolated directly because it has been parsed/validated by Jackson
     * before reaching this method (no injection surface).
     */
    private void setWorkspaceForRls(UUID workspaceId) {
        if (workspaceId == null) return;
        try {
            jdbc.execute("SET LOCAL app.workspace_id = '" + workspaceId + "'");
        } catch (Exception e) {
            log.warn("[pipeline] Failed to set RLS session variable  workspace={}  err={}",
                    workspaceId, e.getMessage());
        }
    }

    private String toJson(Object obj) {
        try {
            return objectMapper.writeValueAsString(obj);
        } catch (JsonProcessingException e) {
            log.warn("[pipeline] JSON serialization failed for {}: {}", obj.getClass().getSimpleName(), e.getMessage());
            return "{}";
        }
    }
}
