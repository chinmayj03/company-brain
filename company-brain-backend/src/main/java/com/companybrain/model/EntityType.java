package com.companybrain.model;

/**
 * Canonical entity-type taxonomy for the Company Brain graph.
 * Maps to the six harness types plus function_node (ADR-0017).
 *
 * Postgres: nodes.entity_type CHECK constraint in V6 migration.
 * Python:   companybrain.store.identity.ALLOWED_ENTITY_TYPES
 */
public enum EntityType {
    COMPONENT("component"),
    SCREEN("screen"),
    API_CONTRACT("api_contract"),
    DATA_MODEL("data_model"),
    ASSUMPTION("assumption"),          // ADR-0017: promoted from node_context
    BUSINESS_CONTEXT("business_context"), // ADR-0017: promoted from node_context
    FUNCTION_NODE("function_node");

    private final String value;

    EntityType(String value) {
        this.value = value;
    }

    public String value() {
        return value;
    }

    public static EntityType fromValue(String value) {
        for (EntityType t : values()) {
            if (t.value.equalsIgnoreCase(value)) {
                return t;
            }
        }
        throw new IllegalArgumentException("Unknown entity_type: " + value);
    }
}
