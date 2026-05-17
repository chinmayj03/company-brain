"""
companybrain.cache — in-process caches for query results and LLM responses.

Public API::

    from companybrain.cache import QueryResultCache, get_query_cache

    cache = get_query_cache()          # process-level singleton
    hit   = cache.get(question, workspace_id)
    if hit is None:
        result = await expensive_llm_call(...)
        cache.put(question, workspace_id, result)
"""
from companybrain.cache.query_cache import QueryResultCache, get_query_cache

__all__ = ["QueryResultCache", "get_query_cache"]
