package com.companybrain.service;

import com.companybrain.dto.ArtifactFreshnessRequest;
import com.companybrain.dto.ArtifactFreshnessResponse;
import com.companybrain.model.Artifact;
import com.companybrain.model.ArtifactChangeEvent;
import com.companybrain.model.ArtifactLink;
import com.companybrain.model.Node;
import com.companybrain.repository.ArtifactChangeEventRepository;
import com.companybrain.repository.ArtifactLinkRepository;
import com.companybrain.repository.ArtifactRepository;
import com.companybrain.repository.NodeRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Central write path for all artifact ingestion.
 *
 * Responsibilities:
 *   1. Compute SHA-256 content hash
 *   2. Upsert the artifact row (insert or update on content change)
 *   3. Emit a change event into artifact_change_events for the dirty-set engine
 *
 * This is the ONLY place dirty-set events are produced. Every downstream
 * consumer (DirtySetService, incremental pipeline) reads from artifact_change_events.
 *
 * Collectors call upsertArtifact() per artifact they emit.
 * PipelineService calls writeArtifactLinks() after graph nodes are written.
 *
 * See ADR-005: Artifact-Centric Knowledge Pipeline.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class ArtifactWriterService {

    private final ArtifactRepository          artifactRepository;
    private final ArtifactLinkRepository      artifactLinkRepository;
    private final ArtifactChangeEventRepository changeEventRepository;
    private final NodeRepository              nodeRepository;

    // ── Artifact upsert ───────────────────────────────────────────────────────

    /**
     * Insert or update an artifact.
     *
     * @param workspaceId  Tenant scope
     * @param kind         Artifact kind (source_file, pr, commit, ticket, ...)
     * @param externalId   Stable source-derived identifier
     * @param content      Raw content string to hash and store
     * @param sourceUri    Canonical back-link to origin (may be null)
     * @param author       Author/system that produced this artifact (may be null)
     * @param metadata     Kind-specific extra fields (may be null)
     * @return             The persisted Artifact (new or updated)
     */
    @Transactional
    public Artifact upsertArtifact(
            UUID workspaceId,
            String kind,
            String externalId,
            String content,
            String sourceUri,
            String author,
            Map<String, Object> metadata
    ) {
        String newHash = sha256(content);

        return artifactRepository
                .findByWorkspaceIdAndKindAndExternalId(workspaceId, kind, externalId)
                .map(existing -> updateIfChanged(existing, content, newHash, sourceUri, author, metadata))
                .orElseGet(() -> insertNew(workspaceId, kind, externalId, content, newHash,
                                           sourceUri, author, metadata));
    }

    // ── Freshness check (incremental pipeline) ────────────────────────────────

    /**
     * Batch freshness check for a set of source_file artifacts.
     *
     * Algorithm (single transaction, two queries):
     *   1. Fetch all stored artifacts whose externalId is in the request set.
     *   2. For each request item compare client-supplied contentHash to stored contentHash.
     *      - Match  → check if the graph has nodes derived from this artifact.
     *                 If nodes exist → fresh=true, return existingEntities.
     *                 If no nodes    → fresh=false (pipeline never wrote entities yet).
     *      - No match or not in DB → fresh=false (new or changed file).
     *
     * One call replaces N individual DB round-trips; the AI service sends the full
     * list of code units it has discovered and gets back a fresh/dirty split.
     */
    @Transactional(readOnly = true)
    public ArtifactFreshnessResponse checkFreshness(ArtifactFreshnessRequest request) {
        UUID workspaceId = request.getWorkspaceId();
        List<ArtifactFreshnessRequest.ArtifactCheck> checks = request.getArtifacts();

        if (checks == null || checks.isEmpty()) {
            return new ArtifactFreshnessResponse(List.of());
        }

        // Build lookup maps from DB (2 queries total)
        List<String> requestedExternalIds = checks.stream()
                .map(ArtifactFreshnessRequest.ArtifactCheck::getExternalId)
                .distinct()
                .toList();

        Map<String, Artifact> storedByExternalId = artifactRepository
                .findByWorkspaceIdAndKindAndExternalIdIn(workspaceId, "source_file", requestedExternalIds)
                .stream()
                .collect(Collectors.toMap(Artifact::getExternalId, a -> a));

        // For the artifacts that hash-match, fetch derived nodes in one query
        List<String> hashMatchIds = checks.stream()
                .filter(c -> {
                    Artifact stored = storedByExternalId.get(c.getExternalId());
                    return stored != null && stored.getContentHash().equals(c.getContentHash());
                })
                .map(ArtifactFreshnessRequest.ArtifactCheck::getExternalId)
                .toList();

        // Map: artifactExternalId → list of nodes derived from it
        Map<String, List<Node>> nodesByArtifactId = new HashMap<>();
        if (!hashMatchIds.isEmpty()) {
            List<Node> derivedNodes = nodeRepository
                    .findByWorkspaceIdAndArtifactExternalIdIn(workspaceId, hashMatchIds);
            for (Node node : derivedNodes) {
                // Attach each node to all hash-matching artifact external_ids it could belong to
                // (the join query already filters correctly, so we group by externalId heuristically)
                // We use node.metadata.file to match — fall back to attaching to all hashMatchIds
                String artifactKey = resolveArtifactExternalId(node, hashMatchIds);
                nodesByArtifactId.computeIfAbsent(artifactKey, k -> new ArrayList<>()).add(node);
            }
        }

        // Build response
        List<ArtifactFreshnessResponse.ArtifactStatus> results = checks.stream().map(check -> {
            Artifact stored = storedByExternalId.get(check.getExternalId());
            boolean hashMatches = stored != null && stored.getContentHash().equals(check.getContentHash());
            List<Node> derivedNodes = nodesByArtifactId.getOrDefault(check.getExternalId(), List.of());
            boolean fresh = hashMatches && !derivedNodes.isEmpty();

            List<ArtifactFreshnessResponse.ExistingEntityDto> existingEntities = fresh
                    ? derivedNodes.stream()
                            .map(n -> ArtifactFreshnessResponse.ExistingEntityDto.builder()
                                    .nodeType(n.getNodeType())
                                    .name(n.getName())
                                    .externalId(n.getExternalId())
                                    .metadata(n.getMetadata() != null ? n.getMetadata() : Map.of())
                                    .build())
                            .toList()
                    : List.of();

            log.debug("[freshness] {}  fresh={}  nodes={}  hashMatch={}",
                    check.getExternalId(), fresh, derivedNodes.size(), hashMatches);

            return ArtifactFreshnessResponse.ArtifactStatus.builder()
                    .externalId(check.getExternalId())
                    .fresh(fresh)
                    .existingEntities(existingEntities)
                    .build();
        }).toList();

        long freshCount = results.stream().filter(ArtifactFreshnessResponse.ArtifactStatus::isFresh).count();
        log.info("[freshness] workspace={}  total={}  fresh={}  dirty={}",
                workspaceId, results.size(), freshCount, results.size() - freshCount);

        return new ArtifactFreshnessResponse(results);
    }

    /**
     * Best-effort match: find which artifact external_id a node belongs to
     * by inspecting its metadata.file field (e.g. "repo/src/main/java/Foo.java").
     * Falls back to first hashMatchId if no match found.
     */
    private String resolveArtifactExternalId(Node node, List<String> candidateArtifactIds) {
        Object fileMeta = node.getMetadata() != null ? node.getMetadata().get("file") : null;
        if (fileMeta instanceof String filePath) {
            // artifact external_id ends with the file path (e.g. "my-repo/src/main/java/Foo.java")
            return candidateArtifactIds.stream()
                    .filter(id -> id.endsWith(filePath))
                    .findFirst()
                    .orElse(candidateArtifactIds.isEmpty() ? "" : candidateArtifactIds.get(0));
        }
        return candidateArtifactIds.isEmpty() ? "" : candidateArtifactIds.get(0);
    }

    // ── Artifact link write ───────────────────────────────────────────────────

    /**
     * Record that a graph node was derived from (or cited in context of) an artifact.
     *
     * Call this AFTER the node has been written so node_id is valid.
     * Existing links with the same (artifact_id, node_id, link_role) are replaced.
     */
    @Transactional
    public void writeArtifactLink(
            UUID workspaceId,
            UUID artifactId,
            UUID nodeId,
            String linkRole,
            double confidence
    ) {
        // Delete stale link if present (idempotent re-run safety)
        artifactLinkRepository.deleteByWorkspaceIdAndNodeIdAndLinkRole(
                workspaceId, nodeId, linkRole);

        ArtifactLink link = ArtifactLink.builder()
                .artifactId(artifactId)
                .nodeId(nodeId)
                .linkRole(linkRole)
                .workspaceId(workspaceId)
                .confidence(java.math.BigDecimal.valueOf(confidence))
                .createdAt(OffsetDateTime.now())
                .build();

        artifactLinkRepository.save(link);
    }

    /**
     * Batch-write artifact links for a node.
     * Replaces all existing 'derived_from' links for the node first.
     */
    @Transactional
    public void writeArtifactLinks(UUID workspaceId, UUID nodeId, List<ArtifactLinkDto> links) {
        for (ArtifactLinkDto dto : links) {
            writeArtifactLink(workspaceId, dto.artifactId(), nodeId, dto.linkRole(), dto.confidence());
        }
    }

    // ── Internal helpers ──────────────────────────────────────────────────────

    private Artifact insertNew(
            UUID workspaceId, String kind, String externalId,
            String content, String contentHash,
            String sourceUri, String author, Map<String, Object> metadata
    ) {
        Artifact artifact = Artifact.builder()
                .id(UUID.randomUUID())
                .workspaceId(workspaceId)
                .kind(kind)
                .externalId(externalId)
                .contentHash(contentHash)
                .contentInline(content)
                .sourceUri(sourceUri)
                .author(author)
                .fetchedAt(OffsetDateTime.now())
                .lastSeenHash(null)
                .metadata(metadata != null ? metadata : Map.of())
                .build();

        artifact = artifactRepository.save(artifact);
        emitChangeEvent(artifact.getId(), workspaceId, "created", null, contentHash);

        log.debug("[artifact] Created  kind={}  externalId={}  hash={}",
                kind, externalId, contentHash.substring(0, 8));
        return artifact;
    }

    private Artifact updateIfChanged(
            Artifact existing, String content, String newHash,
            String sourceUri, String author, Map<String, Object> metadata
    ) {
        if (newHash.equals(existing.getContentHash())) {
            // Content unchanged — no event, just refresh fetched_at
            existing.setFetchedAt(OffsetDateTime.now());
            return artifactRepository.save(existing);
        }

        String oldHash = existing.getContentHash();
        existing.setLastSeenHash(oldHash);
        existing.setContentHash(newHash);
        existing.setContentInline(content);
        existing.setFetchedAt(OffsetDateTime.now());
        if (sourceUri != null) existing.setSourceUri(sourceUri);
        if (author    != null) existing.setAuthor(author);
        if (metadata  != null) existing.setMetadata(metadata);

        existing = artifactRepository.save(existing);
        emitChangeEvent(existing.getId(), existing.getWorkspaceId(), "changed", oldHash, newHash);

        log.info("[artifact] Changed  kind={}  externalId={}  oldHash={}  newHash={}",
                existing.getKind(), existing.getExternalId(),
                oldHash.substring(0, 8), newHash.substring(0, 8));
        return existing;
    }

    private void emitChangeEvent(
            UUID artifactId, UUID workspaceId,
            String eventKind, String oldHash, String newHash
    ) {
        ArtifactChangeEvent event = ArtifactChangeEvent.builder()
                .workspaceId(workspaceId)
                .artifactId(artifactId)
                .eventKind(eventKind)
                .oldHash(oldHash)
                .newHash(newHash)
                .occurredAt(OffsetDateTime.now())
                .build();
        changeEventRepository.save(event);
    }

    // ── SHA-256 helper ────────────────────────────────────────────────────────

    static String sha256(String content) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(
                    content == null ? new byte[0] : content.trim().getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    // ── DTO for batch link writes ─────────────────────────────────────────────

    public record ArtifactLinkDto(UUID artifactId, String linkRole, double confidence) {}
}
