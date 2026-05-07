package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.OffsetDateTime;
import java.util.UUID;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AnnotationResponse {

    private UUID id;
    private UUID nodeId;
    private String annotationType;
    private OffsetDateTime createdAt;
}
