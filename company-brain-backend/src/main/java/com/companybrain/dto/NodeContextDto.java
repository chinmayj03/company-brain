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
public class NodeContextDto {

    private UUID id;
    private String contextType;
    private String title;
    /** Decrypted plaintext of the body. */
    private String body;
    private String author;
    private String sourceUrl;
    private String sourceId;
    private String annotationType;
    private String confidence;
    private OffsetDateTime occurredAt;
}
