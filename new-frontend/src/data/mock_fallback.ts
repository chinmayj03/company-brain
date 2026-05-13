/**
 * mock_fallback.ts — typed port of the prototype's data.js.
 * Used during offline dev and when brain endpoints aren't ready.
 * Replace individual exports with brain_client.ts calls as endpoints land.
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface TimelineEvent {
  at: number;
  kind: 'release' | 'commit' | 'incident';
  label: string;
  date: string;
}

export interface TimeTravelState {
  label: string;
  atFrac: number;
  stats: { affected: number; dirs: number; teams: number; risk: 'LOW' | 'MED' | 'HIGH' };
  summary: string;
  verdictNote: string;
}

export interface CodePreviewLine {
  no: number;
  text: string; // may contain HTML spans for syntax highlighting
}

export interface Citation {
  n: number;
  file: string;
  ln: string;
  what: string;
  tag: 'ts' | 'sql' | 'adr' | 'notion';
  tagLabel: string;
  preview?: CodePreviewLine[];
}

export interface Owner {
  initials: string;
  name: string;
  team: string;
  pct: number;
  last: string;
}

export interface GraphNode {
  id: string;
  x: number;
  y: number;
  label: string;
  sub: string;
  weight: 'high' | 'med' | 'low';
  ring: 1 | 2;
  via?: string;
}

export interface BlastRadiusGraph {
  center: { x: number; y: number; label: string; sub: string };
  nodes: GraphNode[];
}

export interface SourceEntry {
  id: string;
  name: string;
  meta: string;
  state: 'ok' | 'soon';
  sub: string;
}

export interface NavItem {
  id: string;
  label: string;
  count?: string;
  active?: boolean;
}

export interface RecentQuery {
  q: string;
  when: string;
}

export interface AgentEntry {
  name: string;
  mk: string;
  color: string;
  state: 'live' | 'ready';
  qps: string;
}

export interface MockData {
  citations: Citation[];
  owners: Owner[];
  graph: BlastRadiusGraph;
  sources: SourceEntry[];
  navMain: NavItem[];
  suggested: string[];
  recents: RecentQuery[];
  agents: AgentEntry[];
  events: TimelineEvent[];
  states: TimeTravelState[];
}

// ── Data ─────────────────────────────────────────────────────────────────────

export const events: TimelineEvent[] = [
  { at: 0.04, kind: 'release',  label: 'v2.4.0 released',              date: "Nov 12 '25" },
  { at: 0.18, kind: 'commit',   label: 'add `lobName` to customers',   date: "Dec 03 '25" },
  { at: 0.31, kind: 'incident', label: 'P1 — payments webhook drop',   date: "Jan 09 '26" },
  { at: 0.49, kind: 'release',  label: 'v2.5.0 released',              date: "Feb 14 '26" },
  { at: 0.62, kind: 'commit',   label: 'ADR-0048 two-agent extraction',date: "Mar 08 '26" },
  { at: 0.74, kind: 'incident', label: 'P2 — slow query on customers', date: "Apr 02 '26" },
  { at: 0.88, kind: 'release',  label: 'v2.6.0 released',              date: "Apr 28 '26" },
  { at: 0.98, kind: 'commit',   label: 'cache wire-up (ADR-0049)',     date: "May 09 '26" },
];

export const timeTravelStates: TimeTravelState[] = [
  {
    label: "6 mo ago — Nov 12 '25",
    atFrac: 0.04,
    stats: { affected: 23, dirs: 4, teams: 2, risk: 'MED' },
    summary:
      'In November 2025, renaming <b>customer_id</b> touched <b class="num">23</b> files across <b class="num">4</b> directories owned by <b class="num">2</b> teams. The <b>jOOQ DSL</b> wrapper for customers wasn\'t introduced yet — references were largely raw SQL.',
    verdictNote: 'Pre jOOQ wrapper',
  },
  {
    label: "3 mo ago — Feb 14 '26",
    atFrac: 0.49,
    stats: { affected: 34, dirs: 5, teams: 3, risk: 'HIGH' },
    summary:
      'By February 2026, the rename had grown to <b class="num">34</b> files across <b class="num">5</b> directories, <b class="num">3</b> teams. ADR-0042 introduced the jOOQ wrapper but consumers had not migrated.',
    verdictNote: 'During migration',
  },
  {
    label: "Today — May 12 '26",
    atFrac: 1.0,
    stats: { affected: 47, dirs: 6, teams: 3, risk: 'HIGH' },
    summary:
      'Renaming <b>customer_id</b> affects <b class="num">47</b> files across <b class="num">6</b> directories owned by <b class="num">3</b> teams. <b>12</b> SQL chains read it through the jOOQ wrapper; <b>4</b> webhook handlers parse it from JSON; <b>2</b> public API responses expose it as a field.',
    verdictNote: 'Includes jOOQ + webhooks',
  },
];

export const citations: Citation[] = [
  { n: 1, file: 'src/resources/Customer.ts',          ln: '142-156', what: 'Type definition',         tag: 'ts',     tagLabel: 'TS' },
  {
    n: 2, file: 'src/db/queries/customers.sql',        ln: '12-44',  what: '4 SELECT chains',         tag: 'sql',    tagLabel: 'SQL',
    preview: [
      { no: 12, text: '<span class="kw">SELECT</span> id, <span class="hl">customer_id</span>, email, created_at' },
      { no: 13, text: '  <span class="kw">FROM</span> stripe_customers' },
      { no: 14, text: '  <span class="kw">WHERE</span> <span class="hl">customer_id</span> = $1' },
      { no: 15, text: '  <span class="kw">AND</span>   deleted_at <span class="kw">IS NULL</span>;' },
    ],
  },
  { n: 3, file: 'src/webhooks/payment_intent.ts',     ln: '78',     what: 'Webhook payload field',   tag: 'ts',     tagLabel: 'TS' },
  { n: 4, file: 'src/api/public/v3/customers.ts',     ln: '203-219',what: 'Response shape (public API)', tag: 'ts', tagLabel: 'TS' },
  { n: 5, file: 'docs/adr/0042-jooq-wrapper.md',      ln: '§3.2',   what: 'Wrapper introduced; mandates `customerId` in TS', tag: 'adr', tagLabel: 'ADR' },
  { n: 6, file: 'engineering-handbook/Database column naming', ln: '§ Customers', what: 'Notion: explicit `customer_id` convention', tag: 'notion', tagLabel: 'Notion' },
  { n: 7, file: 'src/db/jooq/CustomersDsl.kt',        ln: '88',     what: 'jOOQ field alias',        tag: 'sql',    tagLabel: 'SQL' },
];

export const owners: Owner[] = [
  { initials: 'JM', name: 'Jordan M.',  team: 'Payments',     pct: 41, last: 'Active'   },
  { initials: 'PA', name: 'Priya A.',   team: 'Platform API', pct: 28, last: '4d ago'   },
  { initials: 'SK', name: 'Sam K.',     team: 'Webhooks',     pct: 19, last: '11d ago'  },
];

export const graph: BlastRadiusGraph = {
  center: { x: 50, y: 50, label: 'customer_id', sub: 'PG · customers' },
  nodes: [
    // ring 1
    { id: 'c-ts',   x: 50, y: 18, label: 'Customer.ts',        sub: '142 · TS',     weight: 'high', ring: 1 },
    { id: 'q-sql',  x: 82, y: 32, label: 'customers.sql',      sub: '12-44',        weight: 'high', ring: 1 },
    { id: 'wb-ts',  x: 84, y: 68, label: 'payment_intent.ts', sub: '78 · TS',      weight: 'high', ring: 1 },
    { id: 'api-ts', x: 50, y: 82, label: 'public/v3/customers',sub: '203 · TS',     weight: 'high', ring: 1 },
    { id: 'jooq',   x: 16, y: 68, label: 'CustomersDsl.kt',   sub: '88 · jOOQ',    weight: 'med',  ring: 1 },
    { id: 'adr',    x: 16, y: 32, label: 'ADR-0042',           sub: 'jOOQ wrapper', weight: 'med',  ring: 1 },
    // ring 2
    { id: 'r2-1', x: 68, y: 10, label: 'webhook tests',    sub: '6 specs',     weight: 'med',  ring: 2, via: 'wb-ts'  },
    { id: 'r2-2', x: 94, y: 50, label: 'reports/billing.sql',sub: 'BI',        weight: 'med',  ring: 2, via: 'q-sql'  },
    { id: 'r2-3', x: 68, y: 92, label: 'OpenAPI schema',   sub: 'public',      weight: 'high', ring: 2, via: 'api-ts' },
    { id: 'r2-4', x: 30, y: 92, label: 'sdk-js types',     sub: 'breaking',    weight: 'high', ring: 2, via: 'api-ts' },
    { id: 'r2-5', x: 6,  y: 50, label: 'snowflake mart',   sub: 'analytics',   weight: 'med',  ring: 2, via: 'jooq'   },
    { id: 'r2-6', x: 30, y: 10, label: 'Notion: naming',   sub: 'doc drift',   weight: 'low',  ring: 2, via: 'adr'    },
  ],
};

export const sources: SourceEntry[] = [
  { id: 'gh',     name: 'GitHub · acme/payments',  meta: 'main · syncing live',   state: 'ok',   sub: 'Connected'   },
  { id: 'gh2',    name: 'GitHub · acme/dashboard', meta: 'main · syncing live',   state: 'ok',   sub: 'Connected'   },
  { id: 'bb',     name: 'Bitbucket · acme/infra',  meta: 'main · synced 2m ago',  state: 'ok',   sub: 'Connected'   },
  { id: 'adr',    name: 'ADRs · /docs/adr',        meta: '23 documents',          state: 'ok',   sub: 'In-repo'     },
  { id: 'notion', name: 'Notion',                  meta: 'PRDs & TRDs',           state: 'soon', sub: 'Coming soon' },
  { id: 'jira',   name: 'Jira',                    meta: 'Tickets & epics',       state: 'soon', sub: 'Coming soon' },
];

export const navMain: NavItem[] = [
  { id: 'ask',     label: 'Ask',          active: true },
  { id: 'history', label: 'History',      count: '24'  },
  { id: 'saved',   label: 'Saved',        count: '8'   },
  { id: 'agents',  label: 'Agents · MCP', count: '4'  },
  { id: 'audit',   label: 'Audit log'                  },
];

export const suggested: string[] = [
  'What breaks if I drop the lobName column?',
  'Show me everywhere process.env is read',
  'Who should review a change to webhook signing?',
  'How did the payments handler look 3 months ago?',
];

export const recents: RecentQuery[] = [
  { q: 'Why did the Jan 9 P1 happen on payments webhook?', when: '2h ago'   },
  { q: 'Where does PII flow leave the system?',            when: 'Yesterday' },
  { q: 'List endpoints touched by ADR-0042',               when: '3d ago'   },
];

export const agents: AgentEntry[] = [
  { name: 'Cursor',          mk: 'C', color: '#0F141B', state: 'live',  qps: '47 qpm · 23 seats'  },
  { name: 'Devin',           mk: 'D', color: '#2A57D4', state: 'live',  qps: '12 qpm · 4 seats'   },
  { name: 'Cody',            mk: 'S', color: '#C8553D', state: 'ready', qps: 'awaiting auth'       },
  { name: 'Internal copilot',mk: 'I', color: '#588B6F', state: 'ready', qps: 'POC pending'         },
];

export const MOCK: MockData = {
  citations,
  owners,
  graph,
  sources,
  navMain,
  suggested,
  recents,
  agents,
  events,
  states: timeTravelStates,
};
