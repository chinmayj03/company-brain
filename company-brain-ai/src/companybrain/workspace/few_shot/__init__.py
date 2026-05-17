"""Few-shot bank: capture, persist, and retrieve successful Q&A pairs."""

from companybrain.workspace.few_shot.bank import FewShotBank, FewShotExample
from companybrain.workspace.few_shot.retriever import FewShotRetriever
from companybrain.workspace.few_shot.capture import FewShotCapture

__all__ = [
    "FewShotBank",
    "FewShotExample",
    "FewShotRetriever",
    "FewShotCapture",
]
