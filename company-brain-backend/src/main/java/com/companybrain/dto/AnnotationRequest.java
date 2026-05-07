package com.companybrain.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AnnotationRequest {

    @NotBlank
    private String commitHash;

    @NotBlank
    @Pattern(
        regexp = "business_context|invariant|risk_flag|deprecation_note",
        message = "annotationType must be one of: business_context, invariant, risk_flag, deprecation_note"
    )
    private String annotationType;

    @NotBlank
    private String text;

    private List<String> appliesToFields;
}
