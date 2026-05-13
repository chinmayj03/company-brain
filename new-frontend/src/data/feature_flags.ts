/**
 * Feature flag system for brain ↔ frontend integration.
 *
 * FLAGS:
 *   LIVE_QUERY      — POST /query (AI service) instead of mock answer
 *   LIVE_STREAM     — use /query/stream SSE for token-by-token streaming
 *   LIVE_HEALTH     — GET /health for provider badge in sidebar
 *   LIVE_BLAST      — map QueryResponse.affected_entities → blast-radius graph
 *   LIVE_CITATIONS  — map QueryResponse.cited_entity_urns → citations panel
 *
 * MAGIC KEY:
 *   ⌘ Cmd + Shift + L  (Mac)   — toggle ALL flags on/off instantly (demo flip)
 *   Ctrl + Shift + L    (Win/Linux)
 *
 * URL param:
 *   ?demo=live   — force all flags ON  (share this URL before a demo)
 *   ?demo=mock   — force all flags OFF (safe to share with investors)
 *
 * Persistence: localStorage key "cb_flags" so the toggle survives page reload.
 */

export type FlagName =
  | 'LIVE_QUERY'
  | 'LIVE_STREAM'
  | 'LIVE_HEALTH'
  | 'LIVE_BLAST'
  | 'LIVE_CITATIONS';

export type Flags = Record<FlagName, boolean>;

const STORAGE_KEY = 'cb_flags';
const MAGIC_KEY = 'L'; // ⌘+Shift+L on Mac, Ctrl+Shift+L on Win/Linux

// ── Defaults (all off = fully mock-safe) ────────────────────────────────────

const DEFAULTS: Flags = {
  LIVE_QUERY:     false,
  LIVE_STREAM:    false,
  LIVE_HEALTH:    false,
  LIVE_BLAST:     false,
  LIVE_CITATIONS: false,
};

// ── URL param override: ?demo=live / ?demo=mock ──────────────────────────────

function urlOverride(): Flags | null {
  const param = new URLSearchParams(window.location.search).get('demo');
  if (param === 'live') return Object.fromEntries(Object.keys(DEFAULTS).map(k => [k, true])) as Flags;
  if (param === 'mock') return Object.fromEntries(Object.keys(DEFAULTS).map(k => [k, false])) as Flags;
  return null;
}

// ── Load / save ──────────────────────────────────────────────────────────────

function load(): Flags {
  const override = urlOverride();
  if (override) return override;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { ...DEFAULTS };
}

function save(flags: Flags): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(flags)); } catch { /* ignore */ }
}

// ── Reactive store ────────────────────────────────────────────────────────────

type Listener = (flags: Flags) => void;

class FlagStore {
  private _flags: Flags = load();
  private _listeners: Set<Listener> = new Set();

  get flags(): Flags { return this._flags; }
  get(name: FlagName): boolean { return this._flags[name]; }

  setAll(value: boolean): void {
    this._flags = Object.fromEntries(Object.keys(DEFAULTS).map(k => [k, value])) as Flags;
    save(this._flags);
    this._notify();
  }

  toggle(name: FlagName): void {
    this._flags = { ...this._flags, [name]: !this._flags[name] };
    save(this._flags);
    this._notify();
  }

  toggleAll(): void {
    const anyOn = (Object.values(this._flags) as boolean[]).some(Boolean);
    this.setAll(!anyOn);
  }

  isLive(): boolean {
    return (Object.values(this._flags) as boolean[]).every(Boolean);
  }

  isMock(): boolean {
    return !(Object.values(this._flags) as boolean[]).some(Boolean);
  }

  subscribe(fn: Listener): () => void {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  private _notify(): void {
    this._listeners.forEach(fn => fn(this._flags));
  }
}

export const flags = new FlagStore();

// ── Magic key listener (Ctrl + Shift + L) ────────────────────────────────────

if (typeof window !== 'undefined') {
  window.addEventListener('keydown', (e: KeyboardEvent) => {
    const mod = navigator.platform.startsWith('Mac') ? e.metaKey : e.ctrlKey;
    if (mod && e.shiftKey && e.key === MAGIC_KEY) {
      e.preventDefault();
      flags.toggleAll();
      // Brief visual feedback via document title flash
      const prev = document.title;
      document.title = flags.isMock() ? '🟡 Mock mode' : '🟢 Live mode';
      setTimeout(() => { document.title = prev; }, 1500);
    }
  });
}

// ── React hook ───────────────────────────────────────────────────────────────

import { useEffect, useState, useCallback } from 'react';

export function useFlags(): Flags {
  const [state, setState] = useState<Flags>(() => flags.flags);
  useEffect(() => flags.subscribe(setState), []);
  return state;
}

export function useFlagToggle(): { toggle: (name: FlagName) => void; toggleAll: () => void } {
  const toggle   = useCallback((name: FlagName) => flags.toggle(name), []);
  const toggleAll = useCallback(() => flags.toggleAll(), []);
  return { toggle, toggleAll };
}
