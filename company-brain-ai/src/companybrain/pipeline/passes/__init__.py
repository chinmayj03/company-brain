"""
ADR-0042 extraction passes — language-agnostic LLM pattern-recognizer passes.

Each pass takes a list of CodeUnit + ExtractedEntity objects and emits
ExtractedRelationship edges by asking the LLM a single focused question.

Pass registry (run order matters for co-locality chunking):
  E2  annotation_pass.AnnotationPass       — ANNOTATES edges
  E3  storage_target_pass.StorageTargetPass — DatabaseTable entities
  E5  schema_migration_pass.SchemaMigrationPass — migration-derived schema
  E6  client_call_pass.ClientCallPass       — CALLS_ENDPOINT edges
  E7  test_coverage_pass.TestCoveragePass   — TESTED_BY edges
"""

from companybrain.pipeline.passes.annotation_pass import AnnotationPass
from companybrain.pipeline.passes.storage_target_pass import StorageTargetPass
from companybrain.pipeline.passes.schema_migration_pass import SchemaMigrationPass
from companybrain.pipeline.passes.client_call_pass import ClientCallPass
from companybrain.pipeline.passes.test_coverage_pass import TestCoveragePass

__all__ = [
    "AnnotationPass",
    "StorageTargetPass",
    "SchemaMigrationPass",
    "ClientCallPass",
    "TestCoveragePass",
]
