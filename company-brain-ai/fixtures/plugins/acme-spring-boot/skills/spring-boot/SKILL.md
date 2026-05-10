# Acme Spring Boot conventions

This SKILL.md ships inside the ``acme-spring-boot`` plugin and overrides the
bundled ``spring-boot`` skill when installed (ADR-0052 P6).

## House-style annotations

* ``@AcmeAuditable`` — every public service method is auditable. Treat the
  bytes flowing through it as PII unless explicitly marked otherwise.
* ``@AcmeIdempotent(key=...)`` — wraps an endpoint so retries with the same
  key short-circuit. Look for ``IdempotencyTokenStore`` collaborators.
* ``@AcmeFeatureGate("foo.bar")`` — guards a code path behind LaunchDarkly.
  When extracting endpoints, capture the gate name as a tag on the
  ``ApiEndpoint`` entity.

## Naming conventions

* Service classes end in ``Service`` (``BillingService``, ``LedgerService``).
* Repositories end in ``Repository``.
* Anything ending in ``Adapter`` is an outbound integration with an external
  HTTP/Grpc dependency — emit a corresponding ``External`` entity.

## Quirks worth remembering

* Acme's Spring Boot fork registers controllers under ``/acme-api/v1`` by
  default; ``server.servlet.context-path`` is set in
  ``application-prod.yml`` only.
* ``@AcmeReadOnly`` switches the JPA datasource to the read replica. Reads
  through such methods must never write — flag any save() inside as a bug.
* Pre-2024 endpoints live under ``com.acme.legacy.*``; treat them as
  deprecated and prefer mapping new requests through
  ``com.acme.<bounded-context>.api.*``.

## What to do when extracting

* Always include the ``@AcmeAuditable`` /``@AcmeIdempotent`` /``@AcmeFeatureGate``
  annotations in the entity ``signature``. They look like noise but are
  load-bearing for risk assessment.
* Record the inbound URL prefix (``/acme-api/v1``) on every ``ApiEndpoint``
  so blast-radius reports don't say "endpoint moved" when only the prefix
  changed in config.

## Anti-patterns to ignore

* ``DummyController`` and anything under ``src/test/`` are not real endpoints.
* The ``@AcmeInternalOnly`` annotation removes a class from external API
  surface; do NOT emit ApiEndpoint nodes for those routes.
