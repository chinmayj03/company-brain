package com.companybrain.model;

/**
 * Allowed edge types for the Company Brain structural + semantic graph.
 *
 * The edges table stores edge_type as TEXT (no DB CHECK constraint) so that
 * new types can be added without a schema migration. This enum is the
 * application-level contract; it matches the COMMENT on edges.edge_type
 * written by V7__assumption_business_context_nodes.sql.
 */
public enum EdgeType {
    CALLS,
    EXPOSES,
    CONSUMES_FIELD,
    READS_TABLE,
    WRITES_COLUMN,
    OWNS,
    IMPORTS,
    RENDERS_FIELD,
    CALLS_ENDPOINT,
    VALIDATES,
    DEPENDS_ON,
    RELIES_ON,    // ADR-0017: entity → assumption (entity depends on invariant holding)
    EXPLAINS;     // ADR-0017: business_context → entity (provides rationale / context)

    public String value() {
        return name();
    }

    public static EdgeType fromValue(String value) {
        return EdgeType.valueOf(value.toUpperCase());
    }
}
