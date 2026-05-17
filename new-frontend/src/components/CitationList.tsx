import { useState } from 'react';
import { citations as MOCK_CITATIONS } from '../data/mock_fallback';
import AnswerMarkdown from './AnswerMarkdown';
import type { LiveCitation } from '../data/brain_client';

interface Props {
  liveUrns?: string[];           // legacy: just URN strings
  liveCitations?: LiveCitation[]; // preferred: full citation objects
}

const KIND_COLORS: Record<string, string> = {
  ts:     'var(--accent-primary)',
  sql:    'var(--warning)',
  adr:    'var(--success)',
  notion: 'var(--c-sage)',
  java:   'var(--c-terracotta)',
};

export default function CitationList({ liveUrns, liveCitations }: Props) {
  const [open, setOpen] = useState<number | string | null>(null);

  // Full live citations mode
  if (liveCitations?.length) {
    return (
      <div className="cites">
        <h3>Citations · {liveCitations.length} <span style={{ fontSize: 10, opacity: 0.6 }}>(live)</span></h3>
        {liveCitations.map((c, i) => {
          const isOpen = open === i;
          const color = KIND_COLORS[c.kind ?? ''] ?? 'var(--text-muted)';
          return (
            <div
              key={i}
              className="cite"
              data-expanded={isOpen ? 'true' : undefined}
              onClick={() => setOpen(isOpen ? null : i)}
              style={{ cursor: c.snippet ? 'pointer' : 'default' }}
            >
              <div className="cite-num">{i + 1}</div>
              <div className="cite-body">
                <span className="file">
                  {c.file ?? c.urn}
                  {c.line_range && <span className="ln">:{c.line_range}</span>}
                </span>
                {c.label && <span className="what">{c.label}</span>}
                {c.kind && (
                  <span className="tag" style={{ background: `color-mix(in srgb, ${color} 15%, transparent)`, color, borderColor: `color-mix(in srgb, ${color} 30%, transparent)` }}>
                    {c.kind}
                  </span>
                )}
              </div>
              {isOpen && c.snippet && (
                <div className="cite-prev" style={{ padding: '10px 12px' }}>
                  <AnswerMarkdown content={'```' + (c.kind ?? '') + '\n' + c.snippet + '\n```'} compact />
                </div>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  // Legacy URN string mode
  if (liveUrns?.length) {
    return (
      <div className="cites">
        <h3>Citations · {liveUrns.length} <span style={{ fontSize: 10, opacity: 0.6 }}>(live)</span></h3>
        {liveUrns.map((urn, i) => (
          <div key={i} className="cite">
            <div className="cite-num">{i + 1}</div>
            <div className="cite-body">
              <span className="file" style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{urn}</span>
            </div>
          </div>
        ))}
      </div>
    );
  }

  // Mock mode
  return (
    <div className="cites">
      <h3>Citations · {MOCK_CITATIONS.length}</h3>
      {MOCK_CITATIONS.map((c) => {
        const isOpen = open === c.n;
        return (
          <div
            key={c.n}
            className="cite"
            data-expanded={isOpen ? 'true' : undefined}
            onClick={() => setOpen(isOpen ? null : c.n)}
            style={{ cursor: 'pointer' }}
          >
            <div className="cite-num">{c.n}</div>
            <div className="cite-body">
              <span className="file">{c.file}<span className="ln">:{c.ln}</span></span>
              <span className="what">{c.what}</span>
              <span className={`tag tag--${c.tag}`}>{c.tagLabel}</span>
            </div>
            {isOpen && c.preview && (
              <div className="cite-prev">
                {c.preview.map((p, i) => (
                  <div key={i}><span className="ln-no">{p.no}</span><span dangerouslySetInnerHTML={{ __html: p.text }} /></div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
