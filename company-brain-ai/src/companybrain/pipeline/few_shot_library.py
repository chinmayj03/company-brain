"""
ADR-0060 — Few-shot anchor library for BusinessContext v2 synthesis.

30 (input_snippet, expected_v2_fields) pairs covering the cross-product of
ENTITY_TYPE × COMMON_SHAPE. Each example exists to anchor the model on
"what good looks like" for the seven v2 typed fields:

  is_idempotent, null_handling, transaction_mode, anti_patterns,
  engineering_notes, performance_class, security_class.

Hard budget: serialised JSON of EXAMPLES MUST stay < 6_000 bytes so the
whole block fits inside a single prompt-cache breakpoint. The CI test
`tests/unit/test_business_context_v2.py::test_few_shot_library_fits_cache`
enforces it.

Each example uses only the v2 typed fields plus a short `purpose` —
the v1 narrative fields are demonstrated by the production prompt
itself, not here.
"""

from __future__ import annotations

import json
from typing import Any


# Compact shape on purpose: keys are short, only notable fields are set.
# `i` = input descriptor (entity_type | one-line signature/body summary).
# `o` = expected v2 fields. Omitted fields = default (None / [] / {}).
EXAMPLES: list[dict[str, Any]] = [
    # 1 — Simple GET handler behind auth filter
    {
        "i": "GET /payers/{id}",
        "o": {
            "is_idempotent": True,
            "null_handling": {"id": "throws"},
            "transaction_mode": "read_only",
            "performance_class": "O(1)",
            "security_class": "authenticated",
        },
    },
    # 2 — Repository SELECT with filters
    {
        "i": "Repo findByLob(String lob)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"lob": "unchecked"},
            "transaction_mode": "read_only",
            "performance_class": "O(n)",
        },
    },
    # 3 — Repository UPSERT
    {
        "i": "Repo upsertPayer(Payer); ON CONFLICT DO UPDATE",
        "o": {
            "is_idempotent": True,
            "null_handling": {"p": "throws"},
            "transaction_mode": "read_write",
            "performance_class": "O(1)",
        },
    },
    # 4 — Complex CTE method (network-iq motivating example)
    {
        "i": "getPayerCompetitors; LATERAL unnest+asMaterialized",
        "o": {
            "is_idempotent": True,
            "null_handling": {"base": "throws", "req": "tolerates"},
            "transaction_mode": "read_only",
            "engineering_notes": [
                "LATERAL unnest outer col",
                "asMaterialized avoids double scan",
            ],
            "performance_class": "O(n log n)",
        },
    },
    # 5 — DTO setter
    {
        "i": "DTO setLob(String lob)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"lob": "tolerates"},
            "transaction_mode": "no_transaction",
            "performance_class": "O(1)",
        },
    },
    # 6 — @PreAuthorize controller
    {
        "i": "Ctrl @PreAuthorize(\"hasRole('ADMIN')\") deletePayer(id)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"id": "throws"},
            "transaction_mode": "read_write",
            "performance_class": "O(1)",
            "security_class": "admin_only",
        },
    },
    # 7 — Method with no null check (NPE risk)
    {
        "i": "computeScore(Payer p){p.getName().length()}",
        "o": {
            "is_idempotent": True,
            "null_handling": {"p": "unchecked"},
            "performance_class": "O(1)",
            "anti_patterns": ["unchecked_dereference"],
        },
    },
    # 8 — @Transactional(readOnly=true)
    {
        "i": "@Transactional(readOnly) listAll()",
        "o": {
            "is_idempotent": True,
            "transaction_mode": "read_only",
            "performance_class": "O(n)",
        },
    },
    # 9 — Loop-with-DB-call (N+1)
    {
        "i": "for(id:ids) repo.findById(id); no batch",
        "o": {
            "is_idempotent": True,
            "null_handling": {"ids": "unchecked"},
            "transaction_mode": "read_only",
            "performance_class": "O(n)",
            "anti_patterns": ["potential_n_plus_1"],
        },
    },
    # 10 — Literal-instead-of-constant
    {
        "i": "filter.put(\"lob\",v); use JsonKeyMapping.LOB",
        "o": {
            "is_idempotent": True,
            "performance_class": "O(1)",
            "anti_patterns": ["literal_should_use_constant"],
        },
    },
    # 11 — Async DB write
    {
        "i": "Future<Void> enqueueAudit(Event); persist+publish",
        "o": {
            "is_idempotent": False,
            "null_handling": {"e": "throws"},
            "transaction_mode": "read_write",
            "performance_class": "O(1)",
        },
    },
    # 12 — Error handler @ExceptionHandler
    {
        "i": "@ExceptionHandler(NotFound) handle(ex)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"ex": "checked"},
            "transaction_mode": "no_transaction",
            "performance_class": "O(1)",
            "security_class": "internal_only",
        },
    },
    # 13 — Public health endpoint
    {
        "i": "Ctrl @GetMapping('/health') @PermitAll",
        "o": {
            "is_idempotent": True,
            "transaction_mode": "no_transaction",
            "performance_class": "O(1)",
            "security_class": "public",
        },
    },
    # 14 — Pagination helper
    {
        "i": "Page<X> findAll(Pageable p)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"p": "throws"},
            "transaction_mode": "read_only",
            "performance_class": "O(n)",
        },
    },
    # 15 — Mutation DELETE
    {
        "i": "Repo deleteByPayerId(Long id)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"id": "throws"},
            "transaction_mode": "read_write",
            "performance_class": "O(n)",
        },
    },
    # 16 — INSERT (not idempotent)
    {
        "i": "Repo createClaim(Claim); INSERT",
        "o": {
            "is_idempotent": False,
            "null_handling": {"c": "throws"},
            "transaction_mode": "read_write",
            "performance_class": "O(1)",
        },
    },
    # 17 — Pure mapper (function)
    {
        "i": "static toDto(Payer)->PayerDto(p.id,p.name)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"p": "unchecked"},
            "transaction_mode": "no_transaction",
            "performance_class": "O(1)",
            "anti_patterns": ["unchecked_dereference"],
        },
    },
    # 18 — Method with @NotNull params (checked at boundary)
    {
        "i": "track(@NotNull event, @NotNull Map props)",
        "o": {
            "is_idempotent": False,
            "null_handling": {"event": "throws", "props": "throws"},
            "transaction_mode": "no_transaction",
            "performance_class": "O(1)",
        },
    },
    # 19 — Unbounded recursion / traversal
    {
        "i": "walk(Node n); unbounded recursion",
        "o": {
            "is_idempotent": True,
            "null_handling": {"n": "throws"},
            "performance_class": "unbounded",
            "anti_patterns": ["unbounded_recursion"],
        },
    },
    # 20 — Binary-search style
    {
        "i": "findIndex(int[] sorted, int target)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"sorted": "unchecked", "target": "tolerates"},
            "transaction_mode": "no_transaction",
            "performance_class": "O(log n)",
        },
    },
    # 21 — Sort-then-iterate
    {
        "i": "rank(List<X>){xs.sort(...);return xs}",
        "o": {
            "is_idempotent": True,
            "null_handling": {"xs": "throws"},
            "transaction_mode": "no_transaction",
            "performance_class": "O(n log n)",
            "anti_patterns": ["mutates_input_argument"],
        },
    },
    # 22 — Nested loop matrix op
    {
        "i": "mul(double[][] a, double[][] b)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"a": "unchecked", "b": "unchecked"},
            "performance_class": "O(n²)",
        },
    },
    # 23 — @RolesAllowed("PAYER_ADMIN")
    {
        "i": "Ctrl @RolesAllowed(\"PAYER_ADMIN\") rebuildIndex()",
        "o": {
            "is_idempotent": True,
            "transaction_mode": "read_write",
            "performance_class": "unbounded",
            "security_class": "authorised",
        },
    },
    # 24 — Broad exception catch
    {
        "i": "try{svc.call();}catch(Exception e){log.warn();}",
        "o": {
            "is_idempotent": True,
            "performance_class": "O(1)",
            "anti_patterns": ["broad_exception_catch"],
        },
    },
    # 25 — SELECT FOR UPDATE
    {
        "i": "Repo @Query(\"SELECT...FOR UPDATE\") lockRow(id)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"id": "throws"},
            "transaction_mode": "read_write",
            "performance_class": "O(1)",
            "engineering_notes": ["pessimistic row lock"],
        },
    },
    # 26 — Scheduled job
    {
        "i": "@Scheduled(cron) rollupMetrics()",
        "o": {
            "is_idempotent": True,
            "transaction_mode": "read_write",
            "performance_class": "O(n)",
            "security_class": "internal_only",
        },
    },
    # 27 — Webhook signature verifier
    {
        "i": "verifySig(payload, sig, secret)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"payload": "throws", "sig": "throws", "secret": "throws"},
            "transaction_mode": "no_transaction",
            "performance_class": "O(n)",
            "security_class": "internal_only",
        },
    },
    # 28 — Cache-aside read
    {
        "i": "getX(id); cache.get(id,k->repo.findById(k))",
        "o": {
            "is_idempotent": True,
            "null_handling": {"id": "throws"},
            "transaction_mode": "read_only",
            "performance_class": "O(1)",
            "engineering_notes": ["cache-aside"],
        },
    },
    # 29 — Fan-out HTTP calls
    {
        "i": "fanOut(List<Url>); serial http.get per url",
        "o": {
            "is_idempotent": True,
            "null_handling": {"urls": "throws"},
            "performance_class": "O(n)",
            "anti_patterns": ["serial_remote_calls"],
        },
    },
    # 30 — Builder/fluent setter
    {
        "i": "Builder withLob(String lob)",
        "o": {
            "is_idempotent": True,
            "null_handling": {"lob": "tolerates"},
            "transaction_mode": "no_transaction",
            "performance_class": "O(1)",
        },
    },
]


def serialised_size() -> int:
    """Total byte length of the JSON-serialised library. Used by the cache budget check."""
    return len(json.dumps(EXAMPLES, separators=(",", ":")))


def render_for_prompt() -> str:
    """Render the library as a compact JSON list to drop into the SYSTEM prompt.

    One line per example so the model can scan it like a lookup table; total
    output is the same JSON the cache budget check measures.
    """
    return "\n".join(json.dumps(ex, separators=(",", ":"), sort_keys=True) for ex in EXAMPLES)
