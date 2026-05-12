# network-iq-snapshot

Synthetic, minimal fixture for ADR-0057 acceptance tests.

Intentionally tiny — only the files referenced by the acceptance suite live
here. Not a full copy of the upstream Network IQ codebase.

Files of interest:

- `Dockerfile` — multi-stage build on `openjdk:17-jdk-slim`
- `pom.xml` — declares `spring-boot-starter-web` and `postgresql`
- `src/main/resources/application.yml` — `spring.datasource.url` → postgres
- `docker-compose.yml` — `app` + `postgres:15` services
- `.github/workflows/ci.yml` — runs Maven tests on push/PR
- `src/test/java/.../ReportingUtilsTest.java` — minimal test class
