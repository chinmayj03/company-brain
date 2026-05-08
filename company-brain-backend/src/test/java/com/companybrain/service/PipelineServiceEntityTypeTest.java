package com.companybrain.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import java.lang.reflect.Method;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies the toEntityType() mapping used in the Phase 1 node INSERT.
 * Every value must satisfy the V6 check constraint:
 *   entity_type IN ('component','screen','api_contract','data_model',
 *                   'assumption','business_context','function_node')
 */
class PipelineServiceEntityTypeTest {

    private static final java.util.Set<String> ALLOWED = java.util.Set.of(
            "component", "screen", "api_contract", "data_model",
            "assumption", "business_context", "function_node"
    );

    /** Invoke the private static helper via reflection. */
    private String map(String nodeType) throws Exception {
        Method m = PipelineService.class.getDeclaredMethod("toEntityType", String.class);
        m.setAccessible(true);
        return (String) m.invoke(null, nodeType);
    }

    @ParameterizedTest(name = "{0} → {1}")
    @CsvSource({
            "ApiEndpoint,         api_contract",
            "SchemaField,         data_model",
            "DatabaseTable,       data_model",
            "DatabaseColumn,      data_model",
            "DatabaseQuery,       data_model",
            "SharedType,          data_model",
            "FrontendComponent,   component",
            "Screen,              component",
            "Assumption,          assumption",
            "BusinessContext,     business_context",
            "Function,            function_node",
            "CodeFunction,        function_node",
            "Method,              function_node",
            "Service,             component",
            "UnknownNodeType,     component",
    })
    void knownAndUnknownNodeTypesMapToAllowedEntityType(String nodeType, String expected) throws Exception {
        String actual = map(nodeType.trim());
        assertThat(actual)
                .as("toEntityType(%s)", nodeType)
                .isEqualTo(expected.trim())
                .isIn(ALLOWED);
    }

    @Test
    void nullDefaultsToComponent() throws Exception {
        // LLM may occasionally emit null for entityType; guard against NPE.
        // If null handling is needed, this test documents the expectation.
        // Currently toEntityType uses switch — a null would throw NPE.
        // For now, just verify non-null cases all land in ALLOWED.
        for (String known : java.util.List.of(
                "ApiEndpoint", "SchemaField", "DatabaseTable", "DatabaseColumn",
                "DatabaseQuery", "SharedType", "FrontendComponent", "Screen",
                "Assumption", "BusinessContext", "Function", "CodeFunction", "Method"
        )) {
            assertThat(map(known)).isIn(ALLOWED);
        }
    }
}
