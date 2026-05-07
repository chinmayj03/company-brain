"""
Shared rate-limit-aware retry configuration for all pipeline LLM stages.

Groq free tier TPM windows reset every 60 seconds.  Standard exponential
backoff (max 8–20s) is insufficient — by the time we retry, the window
has not reset and we hit the same 429 immediately.

Policy:
  - On RateLimitError / 429: progressive 20s → 40s → 60s → 90s wait
  - On other errors: short exponential 2s → 4s → 8s → 15s wait
  - Max 4 attempts for extraction (cheap, fast)
  - Max 3 attempts for synthesis/gap (expensive, slower)
"""
from __future__ import annotations

import structlog
from tenacity import RetryCallState, stop_after_attempt, retry

log = structlog.get_logger(__name__)


def _is_rate_limit(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate_limit" in msg or "rate limit" in msg or "ratelimit" in msg


def _rate_limit_wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if exc and _is_rate_limit(exc):
        attempt = retry_state.attempt_number
        wait = min(20 * attempt, 90)
        log.warning("Rate limit — backing off", wait_seconds=wait, attempt=attempt,
                    provider_hint="Groq free tier: 60s TPM window")
        return wait
    # Generic transient error
    return min(2 ** retry_state.attempt_number, 15)


# Ready-to-use kwargs for @retry(**...) — extraction (cheap, 4 attempts)
EXTRACTION_RETRY = dict(stop=stop_after_attempt(4), wait=_rate_limit_wait, reraise=True)

# Ready-to-use kwargs for @retry(**...) — synthesis / gap detection (3 attempts)
SYNTHESIS_RETRY  = dict(stop=stop_after_attempt(3), wait=_rate_limit_wait, reraise=True)
