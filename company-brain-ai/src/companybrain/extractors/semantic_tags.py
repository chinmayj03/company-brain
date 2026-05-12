"""
Semantic tag catalog for ConfigKey entities — ADR-0057 D3.

Raw config keys like ``spring.datasource.url`` aren't useful by themselves;
the brain needs to know that key means "database URL" so queries like
"what database does this codebase use?" can find it.

Patterns are intentionally permissive (.search not .fullmatch) — config
conventions vary across frameworks.
"""
from __future__ import annotations

import re
from typing import Optional

# Order matters: more-specific patterns first. The first match wins.
_CONFIG_SEMANTIC_TAGS: list[tuple[re.Pattern, str]] = [
    # Database
    (re.compile(r"(?:datasource|db|database)\.url$|_?DATABASE_URL$|_?DB_URL$", re.I),         "database_url"),
    (re.compile(r"(?:datasource|db|database)\.username$|_?DB_USER(?:NAME)?$", re.I),          "database_credential"),
    (re.compile(r"(?:datasource|db|database)\.password$|_?DB_PASS(?:WORD)?$", re.I),          "database_credential"),
    (re.compile(r"(?:datasource|db|database)\.driver", re.I),                                  "database_driver"),

    # Cache
    (re.compile(r"redis\.(?:host|url)$|_?REDIS_URL$|memcache", re.I),                          "cache_url"),

    # Messaging / queues
    (re.compile(r"kafka\.|rabbitmq\.|sqs|pubsub|amqp", re.I),                                  "messaging_config"),

    # Secrets / auth
    (re.compile(r"\.api[._-]?key$|_?API_KEY$|secret_?key|client_secret", re.I),                "secret"),
    (re.compile(r"oauth|jwt|saml|sso\.|auth0|cognito", re.I),                                  "auth_config"),

    # Feature flags
    (re.compile(r"feature[._-].*\.enabled$|_?FEATURE_[A-Z0-9_]+$|feature_flag", re.I),         "feature_flag"),

    # Observability
    (re.compile(r"sentry|datadog|newrelic|opentelemetry|otel\.", re.I),                        "observability"),

    # Networking / ports / hosts
    (re.compile(r"\.port$|_?PORT$|server\.port", re.I),                                        "port"),
    (re.compile(r"\.host$|_?HOST$", re.I),                                                     "host"),

    # Scaling / capacity
    (re.compile(r"replicas$|min_instances$|max_instances$|autoscale", re.I),                   "scaling"),

    # Build/runtime
    (re.compile(r"timeout|deadline", re.I),                                                    "timeout"),
    (re.compile(r"log[._-]?level|logging\.level", re.I),                                       "logging"),
]


def tag_config_path(path: str) -> Optional[str]:
    """
    Map a dotted config path (e.g. ``spring.datasource.url``) to a semantic tag
    (e.g. ``"database_url"``). Returns None when no pattern matches.

    Patterns are extensible via plugins (ADR-0052 P6) — for now this catalog
    is closed; org-specific tags get added by editing this file.
    """
    if not path:
        return None
    for pattern, tag in _CONFIG_SEMANTIC_TAGS:
        if pattern.search(path):
            return tag
    return None
