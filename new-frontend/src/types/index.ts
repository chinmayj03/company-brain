// Re-export all mock types so components import from here, not from mock_fallback.ts directly.
// When brain_client.ts lands, only this file needs updating.
export type {
  Citation,
  CodePreviewLine,
  Owner,
  GraphNode,
  BlastRadiusGraph,
  SourceEntry,
  NavItem,
  RecentQuery,
  AgentEntry,
  TimelineEvent,
  TimeTravelState,
  MockData,
} from '../data/mock_fallback';
