import { useState } from 'react';
import { citations } from '../data/mock_fallback';

interface CitationListProps {
  /** When provided (LIVE_CITATIONS on), show live URN list instead of mock */
  liveUrns?: string[];
}

export default function CitationList({ liveUrns }: CitationListProps) {
  const [open, setOpen] = useState<number | null>(2);

  // Live URN mode — simple list until /api/v2/citations lands
  if (liveUrns?.length) {
    return (
      <div className="cites">
        <h3>Citations · {liveUrns.length} <span style={{ fontSize: 10, opacity: 0.6 }}>(live)</span></h3>
        {liveUrns.map((urn, i) => (
          <div key={i} className="cite">
            <div className="cite-num">{i + 1}</div>
            <div className="cite-body">
              <span className="file">{urn}</span>
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="cites">
      <h3>Citations · {citations.length}</h3>
      {citations.map((c) => {
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
              <span className="file">
                {c.file}<span className="ln">:{c.ln}</span>
              </span>
              <span className="what">{c.what}</span>
              <span className={`tag tag--${c.tag}`}>{c.tagLabel}</span>
            </div>
            {isOpen && c.preview && (
              <div className="cite-prev">
                {c.preview.map((p, i) => (
                  <div key={i}>
                    <span className="ln-no">{p.no}</span>
                    <span dangerouslySetInnerHTML={{ __html: p.text }} />
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
