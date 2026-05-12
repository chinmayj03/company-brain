"""
Universal File Extractors — ADR-0057.

Each module under this package implements a deterministic (or LLM-bound)
extractor for a class of files that the brain previously ignored:

  - doc_extractor       Markdown / AsciiDoc / RST / plain-text docs
  - config_extractor    YAML / TOML / properties / .env config files
  - manifest_extractor  POM, package.json, Cargo.toml, requirements.txt, ...
  - infra_extractor     Dockerfile, docker-compose, Makefile, Procfile, Terraform
  - ci_extractor        GitHub Actions / GitLab CI / Jenkins
  - javadoc_extractor   Javadoc / JSDoc / docstrings inside source files
  - test_spec_extractor BehavioralSpec entities (LLM-bound, stub for now)

ADR-0058 adds schema-format awareness:

  - schema_sql          Postgres-flavoured DDL (.sql) → DatabaseTable/Column/Index
  - jooq_binding        jOOQ generated Tables.java → JooqTableBinding/FieldBinding
  - schema_openapi      OpenAPI 2/3 specs → OpenAPIOperation/Schema
  - schema_proto        Protobuf → ProtoMessage/Service/Rpc
  - schema_graphql      GraphQL SDL → GraphQLType/Field/Query
  - schema_resolver     post-pass that resolves jOOQ → DB column URNs and the
                        DOCUMENTS / IMPLEMENTS_RPC / RESOLVES cross-edges.

The router in ``dispatch.py`` maps an extension (or filename pattern) to an
extractor instance. The shared contract lives in ``base.py``. ADR-0058's
extractors register themselves via ``register_schema_extractor`` on first
import of this package — universal-extraction callers see them transparently.
"""
from companybrain.extractors.base import Extractor
from companybrain.extractors.dispatch import (
    EXTRACTOR_DISPATCH,
    extractor_kind_for,
    get_extractor,
    register_schema_extractor,
)

# ── ADR-0058 schema-extractor self-registration ───────────────────────────────
# Registered here so any caller doing `from companybrain.extractors import …`
# picks up the schema dispatchers as well. Order does not matter — the
# dispatch checks ADR-0057 extractors first, and the schema extractors only
# claim file shapes that don't overlap with the universal set.
from companybrain.extractors.schema_sql import SchemaSqlExtractor
from companybrain.extractors.schema_proto import ProtoExtractor
from companybrain.extractors.schema_graphql import GraphQLExtractor
from companybrain.extractors.schema_openapi import OpenAPIExtractor
from companybrain.extractors.jooq_binding import JooqTablesExtractor

_SCHEMA_EXTRACTOR_INSTANCES = (
    SchemaSqlExtractor(),
    ProtoExtractor(),
    GraphQLExtractor(),
    OpenAPIExtractor(),
    JooqTablesExtractor(),
)
for _ex in _SCHEMA_EXTRACTOR_INSTANCES:
    register_schema_extractor(_ex)

__all__ = [
    "Extractor",
    "EXTRACTOR_DISPATCH",
    "extractor_kind_for",
    "get_extractor",
    "register_schema_extractor",
]
