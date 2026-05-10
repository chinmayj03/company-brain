"""
E6 — ClientCallPass: CALLS_ENDPOINT edges from frontend ↔ backend network calls.

Identifies every HTTP/gRPC/GraphQL/WebSocket/queue call in the code and emits
CALLS_ENDPOINT edges from the calling function/component to the ApiEndpoint
(URL normalised to ':param' form).

BRAIN_SKIP_CLIENT_CALL_PASS=true disables this pass.
"""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, Field

from companybrain.llm import TaskRole
from companybrain.models.entities import ExtractedEntity, ExtractedRelationship
from companybrain.pipeline.passes.base import ExtractionPass

_MAX_TOKENS = 1_500


class _ClientCall(BaseModel):
    caller_entity: str        # entity name doing the calling
    http_method: str = "GET"  # GET | POST | PUT | DELETE | PATCH | SUBSCRIBE | PUBLISH
    url_pattern: str          # normalised to ':param' form, e.g. GET /users/:id
    confidence: float = 1.0
    evidence: str = ""


class _ClientCallResponse(BaseModel):
    calls: list[_ClientCall]


_SYSTEM_PROMPT = """\
You are a code analyst specialising in network call detection.

For each entity in the input, identify every OUTBOUND network call:
  HTTP calls: fetch(), axios, requests, urllib, RestTemplate, WebClient,
              @FeignClient, httpx, got, node-fetch, superagent
  gRPC stubs: stub.methodName(), BlockingStub, AsyncStub
  GraphQL: Apollo Client useQuery/useMutation, graphql-request, urql
  WebSocket: new WebSocket(), socket.io, ws library
  Message queues: kafkaProducer.send(), sqs.sendMessage(), rabbitMQ.publish(),
                  @KafkaListener (as a consumer endpoint call)
  RTK Query: api.endpoints, createApi baseQuery
  SWR / React Query: useSWR('/api/...'), useQuery('/api/...')

For EACH call emit:
  caller_entity: EXACT entity name from the list
  http_method: GET | POST | PUT | DELETE | PATCH | any uppercase verb
  url_pattern: normalise URL parameters to ':param' form
    Examples:
      /users/${userId}         → /users/:id
      /api/v1/competitors      → /api/v1/competitors
      `/orders/${orderId}/items` → /orders/:id/items
      {baseUrl}/users          → /users
  confidence: 1.0 if URL is literal string; 0.8 if partially dynamic; 0.7 if inferred
  evidence: the exact call token (≤60 chars)

━━━ FEW-SHOT EXAMPLES ━━━

EXAMPLE 1 — React component with axios:
Entity: CompetitorTable [FrontendComponent], snippet: axios.get('/api/v1/competitors?lob='+lob)
Expected: {"calls": [
  {"caller_entity": "CompetitorTable", "http_method": "GET",
   "url_pattern": "/api/v1/competitors", "confidence": 0.9,
   "evidence": "axios.get('/api/v1/competitors?lob='+lob)"}
]}

EXAMPLE 2 — Python requests library:
Entity: fetch_users [Function], snippet: response = requests.get(f'{BASE_URL}/users/{user_id}')
Expected: {"calls": [
  {"caller_entity": "fetch_users", "http_method": "GET",
   "url_pattern": "/users/:id", "confidence": 0.9,
   "evidence": "requests.get(f'{BASE_URL}/users/{user_id}')"}
]}

EXAMPLE 3 — TypeScript fetch with template literal:
Entity: createOrder [Function], snippet: await fetch(`/api/orders/${customerId}`, {method:'POST', body: JSON.stringify(data)})
Expected: {"calls": [
  {"caller_entity": "createOrder", "http_method": "POST",
   "url_pattern": "/api/orders/:id", "confidence": 1.0,
   "evidence": "fetch('/api/orders/${customerId}', {method:'POST'})"}
]}

Return ONLY valid JSON: {"calls": [...]}. No prose.
"""


class ClientCallPass(ExtractionPass):
    name = "client_call_pass"
    role = TaskRole.FAST
    max_tokens = _MAX_TOKENS
    system_prompt = _SYSTEM_PROMPT

    def _build_user_message(self, entities: list[ExtractedEntity]) -> str:
        # Focus on frontend components and functions that contain HTTP call keywords
        http_keywords = (
            "fetch", "axios", "requests", "http", "client", "stub", "query",
            "useSWR", "useQuery", "baseUrl", "BASE_URL", "endpoint", "api",
        )
        relevant = [
            e for e in entities
            if e.entity_type in (
                "FrontendComponent", "Function", "ApiEndpoint", "Class"
            )
            and e.code_snippet
            and any(kw.lower() in (e.code_snippet or "").lower() for kw in http_keywords)
        ]
        if not relevant:
            return ""

        lines = [f"Entities ({len(relevant)} total — find outbound network calls):"]
        for e in relevant[:25]:
            lines.append(f"\n[{e.entity_type}] {e.name}\nSnippet: {(e.code_snippet or '')[:500]}")
        return "\n".join(lines)

    def _parse_response(
        self, raw: str, entities: list[ExtractedEntity]
    ) -> list[ExtractedRelationship]:
        data = json.loads(raw)
        resp = _ClientCallResponse(**data)

        entity_names = {e.name for e in entities}
        rels: list[ExtractedRelationship] = []
        for call in resp.calls:
            if call.caller_entity not in entity_names:
                continue
            endpoint_name = f"{call.http_method} {call.url_pattern}"
            rels.append(ExtractedRelationship(
                from_entity=call.caller_entity,
                from_type=next(
                    (e.entity_type for e in entities if e.name == call.caller_entity),
                    "Function",
                ),
                edge_type="CALLS_ENDPOINT",
                to_entity=endpoint_name,
                to_type="ApiEndpoint",
                confidence=call.confidence,
                evidence=call.evidence,
            ))
        return rels
