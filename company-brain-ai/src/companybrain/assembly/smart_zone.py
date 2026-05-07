"""SmartZoneAssembler — orchestrates classify → retrieve → expand → MMR → tier → compress."""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import structlog

from companybrain.assembly.classifier import classify, TASK_PARAMS
from companybrain.assembly.compressor import compress
from companybrain.assembly.mmr import mmr_rerank
from companybrain.assembly.renderer import render
from companybrain.assembly.tiering import assign_tiers
from companybrain.assembly.types import SmartZonePayload, TokenBudget
from companybrain.retrieval.embedder import Embedder, make_embedder
from companybrain.retrieval.hybrid_search import HybridSearcher
from companybrain.store.base import BrainEntity, BrainStore
from companybrain.store.identity import workspace_slug_for, parse_urn

log = structlog.get_logger(__name__)


class SmartZoneAssembler:
    def __init__(self, *, brain_root: Path, workspace_id: str,
                 store: BrainStore, neo4j_driver, embedder: Optional[Embedder] = None):
        self.brain_root = Path(brain_root)
        self.workspace_id = workspace_id
        self.workspace_slug = workspace_slug_for(workspace_id)
        self.store = store
        self.neo4j = neo4j_driver
        self.embedder = embedder or make_embedder()
        self.searcher = HybridSearcher(brain_root=self.brain_root,
                                       workspace_slug=self.workspace_slug,
                                       embedder=self.embedder)

    async def assemble(self, *, task: str, entities: list[str] | None = None,
                       budget: TokenBudget | None = None) -> SmartZonePayload:
        budget = budget or TokenBudget()
        task_type = classify(task)
        params = TASK_PARAMS[task_type]

        # 1. Primary retrieval (URNs already given OR hybrid search)
        if entities:
            primary = entities[:40]
            scores = {u: 1.0 for u in primary}
        else:
            hits = self.searcher.search(task, top_k=40)
            primary = [h.urn for h in hits]
            scores = {h.urn: h.score for h in hits}

        # 2. Blast-radius expansion
        expanded: set[str] = set(primary)
        blast: dict[str, list[str]] = {}
        if params["hops"] > 0:
            for seed in primary[:5]:
                neighbours = await self._neighbours(seed, hops=params["hops"],
                                                    direction=params["direction"])
                blast[seed] = neighbours
                expanded.update(neighbours)

        # 3. Hydrate entities
        all_urns = list(expanded)
        loaded: dict[str, BrainEntity] = {}
        for u in all_urns:
            e = await self.store.read(u)
            if e is not None:
                loaded[u] = e

        # 4. MMR rerank (using t1_token text for embeddings — cheap)
        relevant_urns = [u for u in all_urns if u in loaded]
        if len(relevant_urns) > params["t1_top_n"] and self.embedder:
            cand_embs = {
                u: self.embedder.embed(loaded[u].t1_token or loaded[u].qualified_name)
                for u in relevant_urns
            }
            query_emb = self.embedder.embed(task)
            ranked = mmr_rerank(
                query_emb=query_emb, candidate_embs=cand_embs,
                relevance={u: scores.get(u, 0.5) for u in relevant_urns},
                lambda_=params["mmr_lambda"],
                top_k=params["t1_top_n"] + params["t2_top_k"] + 5,
            )
        else:
            ranked = sorted(relevant_urns, key=lambda u: scores.get(u, 0.0), reverse=True)

        # 5. Tier assignment
        payload = assign_tiers(
            ranked_urns=ranked,
            entities=loaded,
            budget=budget,
            t1_top_n=params["t1_top_n"],
            t2_top_k=params["t2_top_k"],
        )
        payload.task = task
        payload.task_type = task_type
        payload.blast_radius = blast

        # 6. Pull business_context for every T2 entity (always included)
        bc_entities = []
        for entry in payload.t2:
            for rel in entry["entity"].get("relationships", []):
                if rel.get("edge_type") == "EXPLAINS":
                    bc = await self.store.read(rel["target_id"])
                    if bc is not None:
                        bc_entities.append(bc.to_dict())
        payload.business_context = bc_entities

        # 7. Task-aware compression on T2
        payload.t2 = [compress(e, task_type) for e in payload.t2]

        # 8. Render
        payload.rendered = render(payload)
        log.info("smart_zone.assembled",
                 task_type=task_type, primary=len(primary),
                 expanded=len(expanded), tiered=len(ranked),
                 tokens_used=payload.tokens_used)
        return payload

    async def _neighbours(self, urn: str, *, hops: int, direction: str) -> list[str]:
        clause = {
            "upstream":   f"<-[*1..{hops}]-",
            "downstream": f"-[*1..{hops}]->",
            "both":       f"-[*1..{hops}]-",
        }[direction]
        q = f"MATCH (n {{id: $urn}}){clause}(m) RETURN DISTINCT m.id AS id LIMIT 30"
        try:
            async with self.neo4j.session() as session:
                result = await session.run(q, urn=urn)
                return [r["id"] for r in await result.data()]
        except Exception as exc:
            log.warning("smart_zone.neighbours_failed", urn=urn, error=str(exc))
            return []
