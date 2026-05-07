package com.companybrain.service;

import com.companybrain.dto.IngestRequest;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.awspring.cloud.sqs.operations.SqsTemplate;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.InvalidKeyException;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

@Service
@Slf4j
public class IngestService {

    private static final String HMAC_ALGORITHM = "HmacSHA256";

    private final SqsTemplate sqsTemplate;
    private final ObjectMapper objectMapper;
    private final String queueName;
    private final String ingestSecret;
    private final String sqsEndpoint;

    public IngestService(
            SqsTemplate sqsTemplate,
            ObjectMapper objectMapper,
            @Value("${queues.ingestion}") String queueName,
            @Value("${app.ingest.secret:dev-ingest-secret}") String ingestSecret,
            @Value("${spring.cloud.aws.sqs.endpoint:}") String sqsEndpoint) {
        this.sqsTemplate = sqsTemplate;
        this.objectMapper = objectMapper;
        this.queueName = queueName;
        this.ingestSecret = ingestSecret;
        this.sqsEndpoint = sqsEndpoint;
    }

    /**
     * Validate the HMAC signature of the ingest payload and enqueue to SQS.
     *
     * @param workspaceId  workspace identifier from X-Workspace-Id header
     * @param signature    HMAC-SHA256 hex signature from X-Agent-Signature header
     * @param agentVersion agent version from X-Agent-Version header
     * @param request      parsed request body
     */
    public void acceptBatch(String workspaceId, String signature, String agentVersion, IngestRequest request) {
        String payload;
        try {
            payload = objectMapper.writeValueAsString(request);
        } catch (JsonProcessingException e) {
            log.error("Failed to serialize ingest request", e);
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid request payload");
        }

        boolean isLocalDev = sqsEndpoint != null &&
                (sqsEndpoint.contains("localhost") || sqsEndpoint.contains("localstack"));

        if (!isLocalDev) {
            validateHmac(payload, signature);
        } else {
            log.debug("Dev/LocalStack mode: skipping HMAC validation for workspace {}", workspaceId);
        }

        try {
            sqsTemplate.send(queueName, payload);
            log.info("Enqueued {} events for workspace {} (agent {})",
                    request.getEvents().size(), workspaceId, agentVersion);
        } catch (Exception e) {
            log.error("Failed to enqueue ingest batch to SQS queue {}: {}", queueName, e.getMessage());
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Failed to enqueue events");
        }
    }

    private void validateHmac(String payload, String providedSignature) {
        try {
            Mac mac = Mac.getInstance(HMAC_ALGORITHM);
            SecretKeySpec keySpec = new SecretKeySpec(
                    ingestSecret.getBytes(StandardCharsets.UTF_8), HMAC_ALGORITHM);
            mac.init(keySpec);
            byte[] expectedBytes = mac.doFinal(payload.getBytes(StandardCharsets.UTF_8));
            String expected = HexFormat.of().formatHex(expectedBytes);

            if (!expected.equals(providedSignature)) {
                log.warn("HMAC validation failed. Expected: {}, Got: {}", expected, providedSignature);
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid HMAC signature");
            }
        } catch (NoSuchAlgorithmException | InvalidKeyException e) {
            log.error("HMAC computation error", e);
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "HMAC computation error");
        }
    }
}
