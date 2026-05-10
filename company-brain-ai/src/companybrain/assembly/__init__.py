"""Smart-zone context assembler — ADR-0018."""
from companybrain.assembly.types import SmartZonePayload, TaskType, TokenBudget

try:
    from companybrain.assembly.smart_zone import SmartZoneAssembler
except ImportError:
    # smart_zone pulls in heavy optional deps (bm25s, qdrant-client).
    # Keep the package importable in environments that only install core deps.
    SmartZoneAssembler = None  # type: ignore[assignment,misc]

__all__ = ["SmartZoneAssembler", "SmartZonePayload", "TaskType", "TokenBudget"]
