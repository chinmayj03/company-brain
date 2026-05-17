"""
Streaming utilities for company-brain — A1.5.

Exports:
  stream_query_response — async generator yielding SSE-formatted strings.
"""
from companybrain.streaming.sse_emitter import stream_query_response

__all__ = ["stream_query_response"]
