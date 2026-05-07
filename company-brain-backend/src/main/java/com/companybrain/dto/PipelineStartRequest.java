package com.companybrain.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

import java.util.List;

/**
 * Frontend → Java: start a context pipeline run for an API endpoint.
 */
@Data
public class PipelineStartRequest {

    @NotBlank
    private String endpointPath;

    private String httpMethod = "GET";

    /** Global fallback branch. Per-repo branches take priority from repos[]. */
    private String branch = "main";

    private List<RepoInput> repos;

    @Data
    public static class RepoInput {
        /** Absolute local path to a cloned repo (takes priority over url for git ops). */
        private String localPath;
        /** GitHub/GitLab URL (used for PR enrichment when localPath is set). */
        private String url;
        /** backend | frontend | shared */
        private String type = "backend";
        /** Branch to analyse for this repo. */
        private String branch = "main";
    }
}
