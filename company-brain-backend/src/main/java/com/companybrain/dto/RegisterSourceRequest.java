package com.companybrain.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.util.Map;

@Data
public class RegisterSourceRequest {

    @NotBlank
    private String kind;

    @NotBlank
    private String displayName;

    @NotNull
    private Map<String, Object> config;

    /** When true, immediately dispatch a pipeline run to index this source. */
    private boolean autoIndex = true;
}
