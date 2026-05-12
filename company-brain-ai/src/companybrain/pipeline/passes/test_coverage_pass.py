"""
E7 — TestCoveragePass: TESTED_BY edges linking test methods to production code.

Identifies which production entity each test method primarily exercises and
emits TESTED_BY edges (from production entity TO test entity — never reversed).

Supports: JUnit, TestNG (Java); pytest, unittest (Python); Mocha, Jest, Vitest
(JS/TS); RSpec (Ruby); xUnit (.NET); Go testing.

BRAIN_SKIP_TEST_COVERAGE_PASS=true disables this pass.
"""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, Field

from companybrain.llm import TaskRole
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship
from companybrain.pipeline.passes.base import ExtractionPass

_MAX_TOKENS = 2_500


class _TestEdge(BaseModel):
    production_entity: str   # the entity being tested (must be in entity list)
    test_entity: str         # the test method/class (must be in entity list)
    confidence: float = 1.0
    evidence: str = ""


class _TestCoverageResponse(BaseModel):
    edges: list[_TestEdge]


_SYSTEM_PROMPT = """\
You are a code analyst specialising in test coverage relationships.

For each TEST method/function in the input, identify which PRODUCTION entity
it primarily exercises. Emit TESTED_BY edges FROM the production entity TO
the test entity (NEVER reversed).

Test frameworks you may encounter (not limited to):
  Java: JUnit 4/5 (@Test, @ParameterizedTest), TestNG, Mockito, AssertJ
  Python: pytest (def test_*, class Test*), unittest.TestCase
  JavaScript/TypeScript: Mocha (it/describe), Jest (test/describe/it),
                         Vitest, Jasmine, Cypress, Playwright
  Ruby: RSpec (describe/it/context), Minitest
  .NET: xUnit ([Fact], [Theory]), NUnit, MSTest
  Go: func Test*(t *testing.T)

CONFIDENCE calibration:
  1.0 — test body explicitly calls the production method by name
        (e.g. service.getPayerCompetitors(...) appears in the test body)
  0.85 — test class/file name follows *ServiceTest → *Service or
         *ControllerTest → *Controller or *Spec → * convention
  0.70 — test is in same package/module as production code and accesses same entity
  SKIP — below 0.70 (ambiguous or no clear production target)

IMPORTANT: Only emit edges where BOTH production_entity AND test_entity are
EXACTLY in the entity name list (no inventing names).

━━━ FEW-SHOT EXAMPLES ━━━

EXAMPLE 1 — Java JUnit with explicit call:
Test entity: getCompetitors_returnsFilteredList [Function]
Production entity: getPayerCompetitors [Function]
Snippet: @Test void getCompetitors_returnsFilteredList() { service.getPayerCompetitors(lob, types); ... }
Expected: {"edges": [
  {"production_entity": "getPayerCompetitors", "test_entity": "getCompetitors_returnsFilteredList", "confidence": 1.0, "evidence": "service.getPayerCompetitors(lob, types)"}
]}

EXAMPLE 2 — Python pytest with naming convention:
Test entity: test_get_competitors [Function], file: test_competitor_service.py
Production entity: get_competitors [Function]
Expected: {"edges": [
  {"production_entity": "get_competitors", "test_entity": "test_get_competitors", "confidence": 0.85, "evidence": "test_competitor_service.py naming convention"}
]}

EXAMPLE 3 — Jest test:
Test entity: renders_competitor_table [Function]
Production entity: CompetitorTable [FrontendComponent]
Snippet: it('renders competitor table', () => { render(<CompetitorTable />); ... })
Expected: {"edges": [
  {"production_entity": "CompetitorTable", "test_entity": "renders_competitor_table", "confidence": 1.0, "evidence": "render(<CompetitorTable />)"}
]}

Return ONLY valid JSON: {"edges": [...]}. No prose.
"""


# Heuristics to detect test entities (language-agnostic)
_TEST_INDICATORS = (
    "test_", "_test", "Test", "Spec", "spec_", "_spec",
    "should_", "it_", "describe_",
)


def _is_test_entity(entity: ExtractedEntity) -> bool:
    name = entity.name or ""
    return (
        any(ind in name for ind in _TEST_INDICATORS)
        or "test" in (entity.file or "").lower()
        or "spec" in (entity.file or "").lower()
    )


class TestCoveragePass(ExtractionPass):
    name = "test_coverage_pass"
    role = TaskRole.FAST
    max_tokens = _MAX_TOKENS
    system_prompt = _SYSTEM_PROMPT

    def _build_user_message(self, entities: list[ExtractedEntity]) -> str:
        test_entities = [e for e in entities if _is_test_entity(e)]
        prod_entities = [e for e in entities if not _is_test_entity(e)]

        if not test_entities:
            return ""

        lines = ["Production entities (find which ones are tested):"]
        for e in prod_entities[:30]:
            lines.append(f"- [{e.entity_type}] {e.name}")

        lines.append(f"\nTest entities ({len(test_entities)} total — identify what each tests):")
        for e in test_entities[:25]:
            snippet = (e.code_snippet or "")[:400]
            lines.append(f"\n[{e.entity_type}] {e.name} (file: {e.file})\nSnippet: {snippet}")

        return "\n".join(lines)

    def _parse_response(
        self, raw: str, entities: list[ExtractedEntity]
    ) -> list[ExtractedRelationship]:
        data = json.loads(raw)
        resp = _TestCoverageResponse(**data)

        entity_names = {e.name for e in entities}
        entity_type_map = {e.name: e.entity_type for e in entities}
        rels: list[ExtractedRelationship] = []

        for edge in resp.edges:
            if (edge.production_entity not in entity_names
                    or edge.test_entity not in entity_names):
                continue
            rels.append(ExtractedRelationship(
                from_entity=edge.production_entity,
                from_type=entity_type_map.get(edge.production_entity, "Function"),
                edge_type="TESTED_BY",
                to_entity=edge.test_entity,
                to_type=entity_type_map.get(edge.test_entity, "Function"),
                confidence=edge.confidence,
                evidence=edge.evidence,
            ))
        return rels
