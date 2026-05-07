package com.companybrain.dto;

import jakarta.validation.constraints.NotEmpty;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class IngestRequest {

    @NotEmpty
    private List<IngestEvent> events;

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class IngestEvent {

        private String type;
        private String edgeType;
        private Map<String, Object> sourceNode;
        private Map<String, Object> targetNode;
        private String observedSource;
        private Double confidence;
        private Map<String, Object> metadata;
    }
}
