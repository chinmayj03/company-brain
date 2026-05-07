from fastapi import APIRouter
from companybrain.llm import get_provider
import structlog

router = APIRouter()
log = structlog.get_logger(__name__)


@router.get("/health")
async def health():
    try:
        provider = get_provider()
        provider_name = provider.provider_name
        status = "ok"
    except Exception as e:
        log.warning("LLM provider unavailable", error=str(e))
        provider_name = "unavailable"
        status = "degraded"

    return {
        "status": status,
        "llm_provider": provider_name,
        "version": "0.1.0",
    }
