package com.companybrain.service;

import com.companybrain.model.Node;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Triggers annotation-driven re-synthesis in the Python AI service.
 *
 * After a user submits an annotation (POST /v1/nodes/{nodeId}/annotations),
 * GraphService calls triggerAsync() on this service.  The call is:
 *   - @Async → runs on a background thread, never blocks the HTTP response
 *   - Fire-and-forget: Python returns 202 immediately; it pushes results
 *     back via the standard /v1/internal/pipeline-result callback
 *
 * This is Task #28 — Human Annotation Feedback Loop.
 *
 * Flow:
 *   User annotates a node
 *       ↓ (async, ~0ms added to HTTP response)
 *   Java POSTs to Python POST /feedback/resynthesise
 *       ↓ (background, ~1-3 LLM calls for Stage 3 only)
 *   Python re-synthesises BusinessContext for the single entity
 *       ↓
 *   Python POSTs slim PipelineResult back to Java /v1/internal/pipeline-result
 *       ↓
 *   Java merges updated BusinessContext + T0/T1 tokens into the graph node
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class ResynthesisService {

    private final WebClient.Builder webClientBuilder;

    @Value("${app.ai-service-url:http://localhost:8001}")
    private String aiServiceUrl;

    @Value("${app.internal-api-key:dev-internal-key}")
    private String internalApiKey;

    @Value("${app.backend-url:http://localhost:8080}")
    private String backendUrl;

    /**
     * Asynchronously trigger re-synthesis for a single annotated node.
     *
     * @param workspaceId     Workspace containing the node
     * @param node            The annotated node (provides externalId, nodeType, metadata)
     * @param annotationType  e.g. "business_context", "invariant", "risk_flag"
     * @param annotationText  The human-written annotation text
     * @param author          Who wrote the annotation (from JWT subject)
     */
    @Async
    public void triggerAsync(
            UUID workspaceId,
            Node node,
            String annotationType,
            String annotationText,
            String author
    ) {
        String externalId  = node.getExternalId();
        String entityName  = node.getName();
        String entityType  = node.getNodeType();
        String entityFile  = _extractFile(node);
        String callbackUrl = backendUrl + "/v1/internal/pipeline-result";

        log.info("[resynthesis] Triggering re-synthesis  entity={}  annotation={}  workspace={}",
                entityName, annotationType, workspaceId);

        Map<String, Object> payload = new HashMap<>();
        payload.put("workspace_id",    workspaceId.toString());
        payload.put("node_id",         node.getId().toString());
        payload.put("external_id",     externalId);
        payload.put("entity_name",     entityName);
        payload.put("entity_type",     entityType);
        payload.put("entity_file",     entityFile);
        payload.put("annotation_type", annotationType);
        payload.put("annotation_text", annotationText);
        payload.put("author",          author != null ? author : "");
        payload.put("callback_url",    callbackUrl);
        payload.put("callback_key",    internalApiKey);

        try {
            webClientBuilder.build()
                    .post()
                    .uri(aiServiceUrl + "/feedback/resynthesise")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(payload)
                    .retrieve()
                    .toBodilessEntity()
                    .timeout(Duration.ofSeconds(5))
                    .onErrorResume(ex -> {
                        log.warn("[resynthesis] Python AI service unavailable — skipping re-synthesis  entity={}  error={}",
                                entityName, ex.getMessage());
                        return Mono.empty();
                    })
                    .subscribe(resp -> {
                        if (resp != null) {
                            log.info("[resynthesis] Re-synthesis accepted by AI service  entity={}  status={}",
                                    entityName, resp.getStatusCode());
                        }
                    });
        } catch (Exception e) {
            // Annotation is already saved — a re-synthesis failure is non-fatal
            log.warn("[resynthesis] Failed to dispatch re-synthesis  entity={}  error={}",
                    entityName, e.getMessage());
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    @SuppressWarnings("unchecked")
    private String _extractFile(Node node) {
        if (node.getMetadata() == null) return "";
        Object file = node.getMetadata().get("file");
        return file != null ? file.toString() : "";
    }
}
