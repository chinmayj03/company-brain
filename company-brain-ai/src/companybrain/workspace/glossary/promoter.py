"""
Glossary promoter: promote candidates to the active glossary.

Applies quality thresholds (min_occurrences, min_source_types) and
optionally enriches promoted terms with one-sentence LLM definitions
via the Haiku-class provider.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from companybrain.workspace.glossary.discoverer import GlossaryCandidate
from companybrain.workspace.tuning_store import WorkspaceTuningStore


class GlossaryPromoter:
    """Promote candidates to the active glossary based on quality thresholds."""

    def __init__(
        self,
        store: WorkspaceTuningStore,
        llm_provider=None,
    ):
        self._store = store
        self._llm = llm_provider  # None = skip definition generation

    def promote_candidates(
        self,
        workspace_id: str,
        candidates: list[GlossaryCandidate],
        min_occurrences: int = 20,
        min_source_types: int = 2,
    ) -> tuple[list[GlossaryCandidate], list[GlossaryCandidate]]:
        """
        Filter and promote candidates that meet quality thresholds.

        Returns (promoted, rejected) lists. Fills definitions via the Haiku
        provider when available; gracefully skips when not.
        """
        promoted: list[GlossaryCandidate] = []
        rejected: list[GlossaryCandidate] = []

        for c in candidates:
            if (
                c.occurrences >= min_occurrences
                and len(c.source_types) >= min_source_types
            ):
                if self._llm and not c.definition:
                    c.definition = self._generate_definition(c)
                c.promoted = True
                promoted.append(c)
            else:
                rejected.append(c)

        self._save_promoted(workspace_id, promoted)
        return promoted, rejected

    async def promote_candidates_async(
        self,
        workspace_id: str,
        candidates: list[GlossaryCandidate],
        min_occurrences: int = 20,
        min_source_types: int = 2,
    ) -> tuple[list[GlossaryCandidate], list[GlossaryCandidate]]:
        """
        Async variant — generates definitions with async LLM calls when the
        provider exposes an async ``chat()`` method. Falls back to the sync
        path if no async provider is available.
        """
        promoted: list[GlossaryCandidate] = []
        rejected: list[GlossaryCandidate] = []

        for c in candidates:
            if (
                c.occurrences >= min_occurrences
                and len(c.source_types) >= min_source_types
            ):
                if self._llm and not c.definition:
                    c.definition = await self._generate_definition_async(c)
                c.promoted = True
                promoted.append(c)
            else:
                rejected.append(c)

        self._save_promoted(workspace_id, promoted)
        return promoted, rejected

    def get_active_glossary(self, workspace_id: str) -> list[dict]:
        """Load promoted glossary terms for a workspace."""
        return self._store.get(workspace_id, "glossary", [])

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _generate_definition(self, candidate: GlossaryCandidate) -> str:
        """Call Haiku (sync bridge over async) to generate a one-sentence definition."""
        if not self._llm or not candidate.contexts:
            return ""
        try:
            # Try to detect a running event loop without deprecation warnings.
            try:
                loop = asyncio.get_running_loop()
                loop_running = True
            except RuntimeError:
                loop_running = False

            if loop_running:
                # We're inside an async context — run in a separate thread's loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run, self._call_llm(candidate)
                    )
                    return future.result(timeout=30)
            else:
                return asyncio.run(self._call_llm(candidate))
        except Exception:
            return ""

    async def _generate_definition_async(self, candidate: GlossaryCandidate) -> str:
        """Async definition generation — preferred path."""
        if not self._llm or not candidate.contexts:
            return ""
        try:
            return await self._call_llm(candidate)
        except Exception:
            return ""

    async def _call_llm(self, candidate: GlossaryCandidate) -> str:
        """Build the Haiku prompt and call the provider."""
        from companybrain.llm import ChatMessage, TaskRole

        prompt = (
            f"In 1 sentence, define the term '{candidate.term}' as used in a "
            f"healthcare software system.\n"
            f"Context examples:\n"
            + "\n".join(f"- {c}" for c in candidate.contexts[:3])
        )
        response = await self._llm.chat(
            [ChatMessage(role="user", content=prompt)],
            role=TaskRole.FAST,
            max_tokens=200,
            temperature=0.1,
        )
        return response.content.strip()[:200]

    def _save_promoted(
        self, workspace_id: str, promoted: list[GlossaryCandidate]
    ) -> None:
        data = [
            {
                "term": c.term,
                "aliases": c.aliases,
                "definition": c.definition,
                "occurrences": c.occurrences,
                "source_types": list(c.source_types),
            }
            for c in promoted
        ]
        self._store.set(workspace_id, "glossary", data)
