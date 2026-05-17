package com.companybrain.dto;

import lombok.Builder;
import lombok.Data;

import java.util.UUID;

@Data
@Builder
public class RegisterSourceResponse {
    private WorkspaceSourceDto source;
    /** Present when autoIndex=true and a pipeline job was dispatched. */
    private UUID jobId;
}
