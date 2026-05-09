package com.companybrain.service;

import com.companybrain.dto.AssembledContext;
import com.companybrain.dto.ContextAssemblyRequest;
import com.companybrain.exception.NodeNotFoundException;
import com.companybrain.model.Edge;
import com.companybrain.model.Node;
import com.companybrain.model.NodeContext;
import com.companybrain.repository.EdgeRepository;
import com.companybrain.repository.NodeContextRepository;
import com.companybrain.repository.NodeRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.stream.Collectors;

/**
 * ContextAssemblerService — tiered knowledge assembly for AI Ask queries.
 *
 * Takes a focal node + question and assembles a structured, token-budget-aware
 * context block for inclusion in the LLM prompt.
 *
 * Algorithm (per ADR-004: Tiered Memory & Context Assembly):
 *
 *   1. BFS from focal node via CALLS/READS_TABLE/READS_COLUMN edges, up to maxHops.
 *   2. Rank all visited nodes by distance from focal (distance=0 is the focal itself).
 *   3. Assign tiers based on rank:
 *        T2 (~600 tok): focal node + up to 2 closest neighbours
 *        T1 (~100 tok): next 10 nodes (by distance)
 *        T0 (~15  tok): remaining nodes (name + type only)
 *   4. Batch-fetch NodeContext entries for T2+T1 nodes (1 DB query).
 *   5. Render each node's block and accumulate until tokenBudget is exhausted.
 *   6. Return AssembledContext with rendered Markdown + traversal metadata.
 *
 * Edge types traversed in both directions:
 *   CALLS, READS_TABLE, READS_COLUMN, RENDERS_FIELD, CALLS_ENDPOINT
 *
 * Token estimation: ceil(characters / 4) — conservative approximation.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class ContextAssemblerService {

    private static final Set<String> TRAVERSAL_EDGE_TYPES = Set.of(
            "CALLS", "READS_TABLE", "READS_COLUMN", "RENDERS_FIELD", "CALLS_ENDPOINT"
    );

    // Tier token budgets (approximate chars-to-tokens at 4 chars/token)
    private static final int T2_TOKEN_TARGET = 600;
    private static final int T1_TOKEN_TARGET = 100;
    private static final int T0_TOKEN_TARGET = 15;

    // Max nodes per tier
    private static final int T2_MAX_NODES = 3;   // focal + 2 closest
    private static final int T1_MAX_NODES = 10;

    private static final int MAX_HOPS_CEILING = 5;

    private final NodeRepository        nodeRepository;
    private final EdgeRepository        edgeRepository;
    private final NodeContextRepository nodeContextRepository;
    private final JdbcTemplate          jdbc;

    // ── Public API ────────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public AssembledContext assemble(ContextAssemblyRequest request) {
        UUID workspaceId = request.getWorkspaceId();
        int  maxHops     = Math.min(Math.max(request.getMaxHops(), 1), MAX_HOPS_CEILING);
        int  tokenBudget = request.getTokenBudget() > 0 ? request.getTokenBudget() : 4096;

        // Internal endpoint — no JWT, RlsInterceptor never set app.workspace_id.
        // Without this, FORCE'd RLS on nodes/edges/node_context filters every row,
        // so resolveFocalNode would 404 even for valid focal nodes.
        if (workspaceId != null) {
            try {
                jdbc.execute("SET LOCAL app.workspace_id = '" + workspaceId + "'");
            } catch (Exception e) {
                log.warn("[assembler] Failed to set RLS session variable  workspace={}  err={}",
                        workspaceId, e.getMessage());
            }
        }

        // ── Step 1: Resolve focal node ────────────────────────────────────────
        Node focalNode = resolveFocalNode(workspaceId, request);

        log.info("[assembler] Assembling context  focal={}  type={}  maxHops={}  budget={}",
                focalNode.getName(), focalNode.getNodeType(), maxHops, tokenBudget);

        // ── Step 2: BFS traversal ─────────────────────────────────────────────
        // Returns nodes in BFS order with their distance from the focal node.
        LinkedHashMap<UUID, Integer> distanceMap = bfsTraverse(workspaceId, focalNode.getId(), maxHops);

        // ── Step 3: Assign tiers ──────────────────────────────────────────────
        List<UUID> allNodeIds = new ArrayList<>(distanceMap.keySet());

        List<UUID> t2Ids = new ArrayList<>();
        List<UUID> t1Ids = new ArrayList<>();
        List<UUID> t0Ids = new ArrayList<>();

        for (UUID nodeId : allNodeIds) {
            int distance = distanceMap.get(nodeId);
            if (t2Ids.size() < T2_MAX_NODES && distance <= 1) {
                t2Ids.add(nodeId);
            } else if (t1Ids.size() < T1_MAX_NODES) {
                t1Ids.add(nodeId);
            } else {
                t0Ids.add(nodeId);
            }
        }

        // ── Step 4: Batch-fetch all node data ─────────────────────────────────
        Map<UUID, Node> nodeMap = loadNodes(workspaceId, allNodeIds);

        // Fetch NodeContext for T2+T1 nodes in one query (T0 only gets name/type)
        Set<UUID> richContextIds = new LinkedHashSet<>(t2Ids);
        richContextIds.addAll(t1Ids);
        Map<UUID, List<NodeContext>> contextMap = loadContexts(workspaceId, richContextIds);

        // ── Step 5: Render and accumulate under budget ────────────────────────
        StringBuilder sb     = new StringBuilder();
        int           tokens = 0;
        int           t2Tok  = 0, t1Tok = 0, t0Tok = 0;
        int           t2Cnt  = 0, t1Cnt = 0, t0Cnt = 0;
        int           included = 0;

        // Header
        String header = String.format(
                "# Knowledge Context for `%s` (%s)\n\n" +
                "_Assembled from %d graph nodes across %d hops._\n\n",
                focalNode.getName(), focalNode.getNodeType(), allNodeIds.size(), maxHops
        );
        sb.append(header);
        tokens += estimateTokens(header);

        // T2 blocks
        for (UUID id : t2Ids) {
            Node node = nodeMap.get(id);
            if (node == null) continue;
            List<NodeContext> contexts = contextMap.getOrDefault(id, List.of());
            String block = renderT2Block(node, contexts, distanceMap.get(id));
            int blockTokens = estimateTokens(block);
            if (tokens + blockTokens > tokenBudget) break;
            sb.append(block);
            tokens += blockTokens;
            t2Tok  += blockTokens;
            t2Cnt++;
            included++;
        }

        // T1 blocks
        if (tokens < tokenBudget) {
            for (UUID id : t1Ids) {
                Node node = nodeMap.get(id);
                if (node == null) continue;
                List<NodeContext> contexts = contextMap.getOrDefault(id, List.of());
                String block = renderT1Block(node, contexts, distanceMap.get(id));
                int blockTokens = estimateTokens(block);
                if (tokens + blockTokens > tokenBudget) break;
                sb.append(block);
                tokens += blockTokens;
                t1Tok  += blockTokens;
                t1Cnt++;
                included++;
            }
        }

        // T0 section — one-liners grouped at the bottom
        if (tokens < tokenBudget && !t0Ids.isEmpty()) {
            StringBuilder t0Section = new StringBuilder("## Other Related Nodes\n\n");
            for (UUID id : t0Ids) {
                Node node = nodeMap.get(id);
                if (node == null) continue;
                String line = renderT0Line(node, distanceMap.get(id));
                int lineTokens = estimateTokens(line);
                if (tokens + lineTokens > tokenBudget) break;
                t0Section.append(line);
                tokens += lineTokens;
                t0Tok  += lineTokens;
                t0Cnt++;
                included++;
            }
            sb.append(t0Section);
        }

        String contextText = sb.toString();

        log.info("[assembler] Done  focal={}  traversed={}  included={}  tokens={}  t2={}  t1={}  t0={}",
                focalNode.getName(), allNodeIds.size(), included, tokens, t2Cnt, t1Cnt, t0Cnt);

        return AssembledContext.builder()
                .contextText(contextText)
                .estimatedTokens(tokens)
                .focalNodeId(focalNode.getId())
                .focalNodeName(focalNode.getName())
                .focalNodeType(focalNode.getNodeType())
                .nodesTraversed(allNodeIds.size())
                .nodesIncluded(included)
                .maxHopsUsed(maxHops)
                .tierSummary(AssembledContext.TierSummary.builder()
                        .t2Count(t2Cnt).t1Count(t1Cnt).t0Count(t0Cnt)
                        .t2Tokens(t2Tok).t1Tokens(t1Tok).t0Tokens(t0Tok)
                        .build())
                .build();
    }

    // ── BFS traversal ─────────────────────────────────────────────────────────

    /**
     * BFS from focalId along CALLS/READS_TABLE etc. edges in both directions.
     *
     * Returns a LinkedHashMap preserving BFS discovery order,
     * mapping nodeId → shortest distance from focal.
     *
     * We traverse both directions (inbound + outbound) so that:
     *   - "Who calls this?" (inbound CALLS) is included
     *   - "What does this call?" (outbound CALLS) is included
     *
     * Nodes are ranked by distance, so the assembler assigns the best tier
     * to the most directly related code.
     */
    private LinkedHashMap<UUID, Integer> bfsTraverse(UUID workspaceId, UUID focalId, int maxHops) {
        LinkedHashMap<UUID, Integer> visited  = new LinkedHashMap<>();
        Deque<UUID>                  queue    = new ArrayDeque<>();
        Map<UUID, Integer>           distance = new HashMap<>();

        visited.put(focalId, 0);
        distance.put(focalId, 0);
        queue.add(focalId);

        while (!queue.isEmpty()) {
            UUID current = queue.poll();
            int  dist    = distance.get(current);
            if (dist >= maxHops) continue;

            // Fetch outbound edges from current node
            List<Edge> outbound = edgeRepository
                    .findByWorkspaceIdAndSource_IdAndIsPrunedFalse(workspaceId, current)
                    .stream()
                    .filter(e -> TRAVERSAL_EDGE_TYPES.contains(e.getEdgeType()))
                    .toList();

            // Fetch inbound edges to current node
            List<Edge> inbound = edgeRepository
                    .findByWorkspaceIdAndTarget_IdAndIsPrunedFalse(workspaceId, current)
                    .stream()
                    .filter(e -> TRAVERSAL_EDGE_TYPES.contains(e.getEdgeType()))
                    .toList();

            for (Edge e : outbound) {
                UUID neighbour = e.getTarget().getId();
                if (!visited.containsKey(neighbour)) {
                    visited.put(neighbour, dist + 1);
                    distance.put(neighbour, dist + 1);
                    queue.add(neighbour);
                }
            }
            for (Edge e : inbound) {
                UUID neighbour = e.getSource().getId();
                if (!visited.containsKey(neighbour)) {
                    visited.put(neighbour, dist + 1);
                    distance.put(neighbour, dist + 1);
                    queue.add(neighbour);
                }
            }
        }

        return visited;
    }

    // ── Data loading ──────────────────────────────────────────────────────────

    private Node resolveFocalNode(UUID workspaceId, ContextAssemblyRequest request) {
        if (request.getFocalNodeId() != null) {
            return nodeRepository.findById(request.getFocalNodeId())
                    .filter(n -> n.getWorkspaceId().equals(workspaceId))
                    .orElseThrow(() -> new NodeNotFoundException(request.getFocalNodeId()));
        }
        if (request.getFocalExternalId() != null) {
            return nodeRepository.findByWorkspaceIdAndExternalId(workspaceId, request.getFocalExternalId())
                    .orElseThrow(() -> new RuntimeException(
                            "Node not found for externalId: " + request.getFocalExternalId()));
        }
        throw new IllegalArgumentException("Either focalNodeId or focalExternalId must be provided");
    }

    private Map<UUID, Node> loadNodes(UUID workspaceId, List<UUID> nodeIds) {
        // JPA findAllById is OK for moderate sets; the BFS cap of ~100 nodes keeps this safe.
        return nodeRepository.findAllById(nodeIds).stream()
                .filter(n -> n.getWorkspaceId().equals(workspaceId))
                .collect(Collectors.toMap(Node::getId, n -> n));
    }

    private Map<UUID, List<NodeContext>> loadContexts(UUID workspaceId, Collection<UUID> nodeIds) {
        if (nodeIds.isEmpty()) return Map.of();
        return nodeContextRepository
                .findByWorkspaceIdAndNodeIdIn(workspaceId, nodeIds)
                .stream()
                .collect(Collectors.groupingBy(nc -> nc.getNode().getId()));
    }

    // ── Tier renderers ────────────────────────────────────────────────────────

    /**
     * T2 — Full block (~600 tokens).
     * Includes: name, type, file, signature, business context, invariants, change risk,
     *           annotations, git history summary.
     */
    private String renderT2Block(Node node, List<NodeContext> contexts, int distance) {
        StringBuilder sb = new StringBuilder();
        Map<String, Object> meta = node.getMetadata() != null ? node.getMetadata() : Map.of();

        sb.append(String.format("## %s `%s`%s\n\n",
                node.getNodeType(), node.getName(),
                distance == 0 ? " _(focal)_" : String.format(" _(distance: %d)_", distance)));

        // Structural facts from metadata
        appendIfPresent(sb, meta, "file",       "**File:**");
        appendIfPresent(sb, meta, "repo",       "**Repo:**");
        appendIfPresent(sb, meta, "signature",  "**Signature:**");

        // Business context from NodeContext entries
        Optional<NodeContext> synthesis = contexts.stream()
                .filter(c -> "llm_synthesis".equals(c.getContextType()))
                .findFirst();

        if (synthesis.isPresent()) {
            String body = decodeBody(synthesis.get().getBody());
            if (body != null && !body.isBlank()) {
                sb.append("\n**Business Context:**\n").append(body.strip()).append("\n");
            }
        }

        // Invariants
        List<NodeContext> invariants = contexts.stream()
                .filter(c -> "invariant".equals(c.getContextType())
                          || "invariant".equals(c.getAnnotationType()))
                .limit(5).toList();
        if (!invariants.isEmpty()) {
            sb.append("\n**Invariants:**\n");
            invariants.forEach(nc -> {
                String body = decodeBody(nc.getBody());
                if (body != null) sb.append("- ").append(body.strip()).append("\n");
            });
        }

        // Risk flags + annotations
        List<NodeContext> annotations = contexts.stream()
                .filter(c -> "user_annotation".equals(c.getContextType())
                          || "risk_flag".equals(c.getContextType()))
                .limit(3).toList();
        if (!annotations.isEmpty()) {
            sb.append("\n**Annotations:**\n");
            annotations.forEach(nc -> {
                String body = decodeBody(nc.getBody());
                String type = nc.getAnnotationType() != null ? nc.getAnnotationType() : nc.getContextType();
                if (body != null) sb.append("- [").append(type).append("] ").append(body.strip()).append("\n");
            });
        }

        // Change risk from metadata
        Object changeRisk = meta.get("changeRisk");
        if (changeRisk != null) {
            sb.append(String.format("\n**Change Risk:** %s", changeRisk));
            Object reason = meta.get("changeRiskReason");
            if (reason != null) sb.append(" — ").append(reason);
            sb.append("\n");
        }

        // Query text for database entities
        Object queryText = meta.get("queryText");
        if (queryText != null) {
            sb.append("\n**Query:**\n```sql\n").append(queryText).append("\n```\n");
        }

        sb.append("\n");
        return sb.toString();
    }

    /**
     * T1 — Summary block (~100 tokens).
     * Includes: name, type, distance, one-line purpose (from synthesis or title).
     */
    private String renderT1Block(Node node, List<NodeContext> contexts, int distance) {
        String purpose = contexts.stream()
                .filter(c -> "llm_synthesis".equals(c.getContextType()))
                .map(nc -> {
                    String body = decodeBody(nc.getBody());
                    return body != null ? truncate(body.strip(), 200) : null;
                })
                .filter(Objects::nonNull)
                .findFirst()
                .orElseGet(() -> contexts.stream()
                        .filter(c -> c.getTitle() != null)
                        .map(NodeContext::getTitle)
                        .findFirst()
                        .orElse("_No context synthesised yet._"));

        Map<String, Object> meta = node.getMetadata() != null ? node.getMetadata() : Map.of();
        String file = meta.containsKey("file") ? " `" + meta.get("file") + "`" : "";

        return String.format("### %s `%s`%s _(hop %d)_\n%s\n\n",
                node.getNodeType(), node.getName(), file, distance, purpose);
    }

    /**
     * T0 — One-liner (~15 tokens).
     * Just enough for the LLM to know the node exists.
     */
    private String renderT0Line(Node node, int distance) {
        Map<String, Object> meta = node.getMetadata() != null ? node.getMetadata() : Map.of();
        String file = meta.containsKey("file") ? " (" + meta.get("file") + ")" : "";
        return String.format("- **%s** `%s`%s (hop %d)\n", node.getNodeType(), node.getName(), file, distance);
    }

    // ── Utility helpers ───────────────────────────────────────────────────────

    private static String decodeBody(byte[] body) {
        if (body == null || body.length == 0) return null;
        return new String(body, StandardCharsets.UTF_8);
    }

    private static String truncate(String s, int maxChars) {
        if (s == null || s.length() <= maxChars) return s;
        return s.substring(0, maxChars) + "…";
    }

    private static void appendIfPresent(StringBuilder sb, Map<String, Object> meta, String key, String label) {
        Object val = meta.get(key);
        if (val != null && !val.toString().isBlank()) {
            sb.append(label).append(" ").append(val).append("\n");
        }
    }

    /**
     * Token count approximation: characters / 4 (standard LLM heuristic).
     * Safe to use for budget decisions — real count may be ~10% different.
     */
    static int estimateTokens(String text) {
        if (text == null || text.isEmpty()) return 0;
        return (int) Math.ceil(text.length() / 4.0);
    }
}
