package com.companybrain.service;

import com.companybrain.dto.*;
import com.companybrain.exception.NodeNotFoundException;
import com.companybrain.model.Edge;
import com.companybrain.model.Node;
import com.companybrain.model.NodeContext;
import com.companybrain.repository.EdgeRepository;
import com.companybrain.repository.NodeContextRepository;
import com.companybrain.repository.NodeRepository;
import com.companybrain.security.WorkspaceContext;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
@Slf4j
public class GraphService {

    private final NodeRepository nodeRepository;
    private final EdgeRepository edgeRepository;
    private final NodeContextRepository nodeContextRepository;
    private final WorkspaceContext workspaceContext;
    private final ResynthesisService resynthesisService;

    // ----------------------------------------------------------------
    // Node context
    // ----------------------------------------------------------------

    @Transactional(readOnly = true)
    public NodeContextListResponse getNodeContext(UUID workspaceId, UUID nodeId, int page, int size) {
        PageRequest pageable = PageRequest.of(page, size);
        List<NodeContext> entries = nodeContextRepository
                .findByWorkspaceIdAndNode_IdOrderByOccurredAtDesc(workspaceId, nodeId, pageable);
        long total = nodeContextRepository.countByWorkspaceIdAndNode_Id(workspaceId, nodeId);

        List<NodeContextDto> dtos = entries.stream()
                .map(this::toContextDto)
                .collect(Collectors.toList());

        return NodeContextListResponse.builder()
                .entries(dtos)
                .total((int) total)
                .page(page)
                .build();
    }

    private NodeContextDto toContextDto(NodeContext nc) {
        String bodyText = null;
        if (nc.getBody() != null) {
            // MVP: body stored as plain UTF-8; enterprise path would decrypt via KMS first
            bodyText = new String(nc.getBody(), StandardCharsets.UTF_8);
        }
        return NodeContextDto.builder()
                .id(nc.getId())
                .contextType(nc.getContextType())
                .title(nc.getTitle())
                .body(bodyText)
                .author(nc.getAuthor())
                .sourceUrl(nc.getSourceUrl())
                .sourceId(nc.getSourceId())
                .annotationType(nc.getAnnotationType())
                .confidence(nc.getConfidence())
                .occurredAt(nc.getOccurredAt())
                .build();
    }

    // ----------------------------------------------------------------
    // Dependency navigation
    // ----------------------------------------------------------------

    @Transactional(readOnly = true)
    public NodeListResponse getDependents(UUID workspaceId, UUID nodeId, String edgeType) {
        List<Edge> edges = edgeType == null
                ? edgeRepository.findByWorkspaceIdAndTarget_IdAndIsPrunedFalse(workspaceId, nodeId)
                : edgeRepository.findByWorkspaceIdAndTarget_IdAndEdgeTypeAndIsPrunedFalse(workspaceId, nodeId, edgeType);

        List<NodeDto> nodes = edges.stream()
                .map(e -> toNodeDto(e.getSource()))
                .distinct()
                .collect(Collectors.toList());

        return NodeListResponse.builder().nodes(nodes).total(nodes.size()).build();
    }

    @Transactional(readOnly = true)
    public NodeListResponse getDependencies(UUID workspaceId, UUID nodeId, String edgeType) {
        List<Edge> edges = edgeType == null
                ? edgeRepository.findByWorkspaceIdAndSource_IdAndIsPrunedFalse(workspaceId, nodeId)
                : edgeRepository.findByWorkspaceIdAndSource_IdAndEdgeTypeAndIsPrunedFalse(workspaceId, nodeId, edgeType);

        List<NodeDto> nodes = edges.stream()
                .map(e -> toNodeDto(e.getTarget()))
                .distinct()
                .collect(Collectors.toList());

        return NodeListResponse.builder().nodes(nodes).total(nodes.size()).build();
    }

    // ----------------------------------------------------------------
    // Service graph — 2-hop neighbourhood
    // ----------------------------------------------------------------

    @Transactional(readOnly = true)
    public GraphResponse getServiceGraph(UUID workspaceId, UUID nodeId) {
        // Collect 1-hop outbound and inbound edges
        List<Edge> hop1Out = edgeRepository.findByWorkspaceIdAndSource_IdAndIsPrunedFalse(workspaceId, nodeId);
        List<Edge> hop1In  = edgeRepository.findByWorkspaceIdAndTarget_IdAndIsPrunedFalse(workspaceId, nodeId);

        Set<UUID> visited = new LinkedHashSet<>();
        visited.add(nodeId);
        List<Edge> allEdges = new ArrayList<>();
        allEdges.addAll(hop1Out);
        allEdges.addAll(hop1In);

        // Collect 1-hop neighbours
        Set<UUID> hop1Nodes = new LinkedHashSet<>();
        for (Edge e : hop1Out) hop1Nodes.add(e.getTarget().getId());
        for (Edge e : hop1In)  hop1Nodes.add(e.getSource().getId());

        // Fetch 2nd hop from each 1-hop neighbour
        for (UUID neighbourId : hop1Nodes) {
            if (!visited.contains(neighbourId)) {
                visited.add(neighbourId);
                List<Edge> hop2Out = edgeRepository
                        .findByWorkspaceIdAndSource_IdAndIsPrunedFalse(workspaceId, neighbourId);
                List<Edge> hop2In  = edgeRepository
                        .findByWorkspaceIdAndTarget_IdAndIsPrunedFalse(workspaceId, neighbourId);
                allEdges.addAll(hop2Out);
                allEdges.addAll(hop2In);
            }
        }

        // Deduplicate edges and collect all unique nodes
        Map<UUID, Edge> edgeMap = new LinkedHashMap<>();
        Map<UUID, Node> nodeMap = new LinkedHashMap<>();

        for (Edge e : allEdges) {
            edgeMap.putIfAbsent(e.getId(), e);
            nodeMap.putIfAbsent(e.getSource().getId(), e.getSource());
            nodeMap.putIfAbsent(e.getTarget().getId(), e.getTarget());
        }

        // Always include the origin node
        nodeRepository.findById(nodeId).ifPresent(n -> nodeMap.putIfAbsent(n.getId(), n));

        List<NodeDto> nodeDtos = nodeMap.values().stream()
                .map(this::toNodeDto)
                .collect(Collectors.toList());

        List<EdgeDto> edgeDtos = edgeMap.values().stream()
                .map(this::toEdgeDto)
                .collect(Collectors.toList());

        return GraphResponse.builder().nodes(nodeDtos).edges(edgeDtos).build();
    }

    // ----------------------------------------------------------------
    // Search
    // ----------------------------------------------------------------

    @Transactional(readOnly = true)
    public NodeListResponse search(UUID workspaceId, String q, String nodeType, int limit) {
        PageRequest pageable = PageRequest.of(0, limit);
        List<Node> results;
        if (nodeType != null && !nodeType.isBlank()) {
            // Filter by type first, then further filter by name in memory (simple MVP approach)
            results = nodeRepository.findByWorkspaceIdAndNodeType(workspaceId, nodeType, pageable)
                    .stream()
                    .filter(n -> n.getName().toLowerCase().contains(q.toLowerCase()))
                    .collect(Collectors.toList());
        } else {
            results = nodeRepository.searchByName(workspaceId, q, pageable);
        }

        List<NodeDto> dtos = results.stream().map(this::toNodeDto).collect(Collectors.toList());
        return NodeListResponse.builder().nodes(dtos).total(dtos.size()).build();
    }

    // ----------------------------------------------------------------
    // Annotations
    // ----------------------------------------------------------------

    @Transactional
    public AnnotationResponse addAnnotation(UUID workspaceId, UUID nodeId, AnnotationRequest request) {
        Node node = nodeRepository.findById(nodeId)
                .filter(n -> n.getWorkspaceId().equals(workspaceId))
                .orElseThrow(() -> new NodeNotFoundException(nodeId));

        NodeContext context = NodeContext.builder()
                .workspaceId(workspaceId)
                .node(node)
                .contextType("user_annotation")
                .annotationType(request.getAnnotationType())
                .title(request.getAnnotationType())
                .body(request.getText().getBytes(StandardCharsets.UTF_8))
                .sourceId(request.getCommitHash())
                .confidence("high")
                .occurredAt(OffsetDateTime.now())
                .appliesToFields(request.getAppliesToFields() != null
                        ? request.getAppliesToFields().toArray(new String[0])
                        : null)
                .build();

        NodeContext saved = nodeContextRepository.save(context);

        // Trigger async re-synthesis in the Python AI service.
        // This runs on a background thread and does NOT delay the HTTP response.
        // Python will re-run Stage 3 (ContextSynthesizer) + Stage 3.5 (MemoryTokenizer)
        // for this entity and push the updated BusinessContext back via pipeline-result.
        resynthesisService.triggerAsync(
                workspaceId,
                node,
                request.getAnnotationType(),
                request.getText(),
                ""   // author — extend WorkspaceContext with userId claim when auth is complete
        );

        return AnnotationResponse.builder()
                .id(saved.getId())
                .nodeId(nodeId)
                .annotationType(saved.getAnnotationType())
                .createdAt(saved.getCreatedAt())
                .build();
    }

    // ----------------------------------------------------------------
    // Mapping helpers
    // ----------------------------------------------------------------

    private NodeDto toNodeDto(Node n) {
        return NodeDto.builder()
                .id(n.getId())
                .nodeType(n.getNodeType())
                .externalId(n.getExternalId())
                .name(n.getName())
                .metadata(n.getMetadata())
                .updatedAt(n.getUpdatedAt())
                .build();
    }

    private EdgeDto toEdgeDto(Edge e) {
        return EdgeDto.builder()
                .id(e.getId())
                .edgeType(e.getEdgeType())
                .sourceId(e.getSource().getId())
                .sourceName(e.getSource().getName())
                .targetId(e.getTarget().getId())
                .targetName(e.getTarget().getName())
                .confidence(e.getConfidence())
                .source(e.getObservedSource())
                .lastSeen(e.getLastSeen())
                .build();
    }
}
