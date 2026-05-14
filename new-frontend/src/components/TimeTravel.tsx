import { useRef } from 'react';
import { events, timeTravelStates } from '../data/mock_fallback';

interface TimeTravelProps {
  position: number;
  setPosition: (p: number) => void;
}

const IconClock = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
    <circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>
  </svg>
);

export default function TimeTravel({ position, setPosition }: TimeTravelProps) {
  const trackRef = useRef<HTMLDivElement>(null);

  function onPointer(e: React.PointerEvent<HTMLDivElement>) {
    if (e.buttons === 0 && e.type !== 'pointerdown') return;
    const rect = trackRef.current!.getBoundingClientRect();
    const x = Math.min(Math.max(e.clientX - rect.left, 0), rect.width);
    setPosition(x / rect.width);
  }

  const current = timeTravelStates.reduce((acc, s) =>
    Math.abs(s.atFrac - position) < Math.abs(acc.atFrac - position) ? s : acc,
    timeTravelStates[0]
  );
  const delta = current.atFrac === 1.0 ? 'live' : current.label.split('—')[0].trim();

  return (
    <div className="tt-bar">
      <div className="tt-head">
        <span className="lab"><IconClock /> Time travel</span>
        <span className="now-pill">
          <span>{current.label}</span>
          <span className="delta">· {delta === 'live' ? 'live' : 'historical'}</span>
        </span>
      </div>
      <div
        className="tt-track"
        ref={trackRef}
        onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); onPointer(e); }}
        onPointerMove={onPointer}
        onPointerUp={(e) => { try { e.currentTarget.releasePointerCapture(e.pointerId); } catch (_) {} }}
        style={{ touchAction: 'none' }}
      >
        <div className="tt-rail" />
        <div className="tt-fill" style={{ width: `${position * 100}%` }} />
        {[0.25, 0.5, 0.75].map((t) => (
          <div key={t} className="tt-tick" style={{ left: `${t * 100}%` }} />
        ))}
        {events.map((ev, i) => (
          <div key={i} className="tt-event" data-kind={ev.kind} style={{ left: `${ev.at * 100}%` }} title={`${ev.date} · ${ev.label}`} />
        ))}
        <div className="tt-handle" style={{ left: `${position * 100}%` }} />
      </div>
      <div className="tt-labels">
        <span>Nov '25</span><span>Jan '26</span><span>Mar '26</span><span>Today</span>
      </div>
      <div className="tt-legend">
        <span className="l"><span className="s" data-k="release" /> release</span>
        <span className="l"><span className="s" /> commit</span>
        <span className="l"><span className="s" data-k="incident" /> incident</span>
        <span className="l" style={{ marginLeft: 'auto' }}>Drag handle to query state at that date</span>
      </div>
    </div>
  );
}
