import { suggested as MOCK_SUGGESTED } from '../data/mock_fallback';

interface SuggestedProps {
  onAsk: (q: string) => void;
  /** Live follow-up questions from brain; falls back to mock chips if absent */
  overrides?: string[];
}

const IconSparkle = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
    <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>
  </svg>
);

export default function Suggested({ onAsk, overrides }: SuggestedProps) {
  const list = overrides ?? MOCK_SUGGESTED;
  return (
    <div className="suggested">
      <span className="lbl">{overrides ? 'Follow-up' : 'Try'}</span>
      {list.map((q, i) => (
        <button className="sg" key={i} onClick={() => onAsk(q)} title={q}>
          <IconSparkle /> {q}
        </button>
      ))}
    </div>
  );
}
