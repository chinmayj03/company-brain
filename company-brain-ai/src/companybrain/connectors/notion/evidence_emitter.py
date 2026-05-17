"""
B1.4 Notion connector — evidence emitter.

Converts a fetched SourceArtifact into BrainEntity records suitable for
ingestion into any BrainStore implementation.

One BrainEntity is produced per Notion page (document-level granularity).
The entity_type is "notion_page" and the id follows the canonical
companybrain URN convention where possible.
"""
from __future__ import annotations

from companybrain.connectors.base import SourceArtifact
from companybrain.store.base import BrainEntity


def artifact_to_brain_entities(artifact: SourceArtifact) -> list[BrainEntity]:
    """
    Convert a fetched Notion SourceArtifact to a list of BrainEntity records.

    Returns an empty list if the artifact has no content.

    The produced entity uses:
      id             = "notion:{page_id}"
      entity_type    = "notion_page"
      repo           = "notion"
      file           = artifact.url
      qualified_name = artifact.title
      t1_summary     = first 500 chars of content
      raw_content    = full content (stored in metadata)
    """
    if not artifact.content:
        return []

    entity = BrainEntity(
        id=f"notion:{artifact.id}",
        entity_type="notion_page",
        repo="notion",
        file=artifact.url or artifact.id,
        qualified_name=artifact.title,
        t1_summary=artifact.content[:500],
        metadata={
            "source_type": "notion",
            "page_id": artifact.id,
            "url": artifact.url,
            "last_edited_time": artifact.metadata.get("last_edited_time"),
            "entity_mentions": artifact.metadata.get("entity_mentions", []),
            "block_count": artifact.metadata.get("block_count", 0),
            "raw_content": artifact.content,
        },
    )
    return [entity]
