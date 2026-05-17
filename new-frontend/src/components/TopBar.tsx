import { useEffect, useState } from 'react';
import { useRepoStore } from '../store/repo_store';
import { useWorkspaceStore } from '../store/workspace_store';
import { getMcpAgents } from '../data/brain_client';

interface TopBarProps {
  crumb: string;
}

const IconGitBranch = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 12, height: 12 }}>
    <circle cx="12" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>
    <path d="M12 8v6"/><path d="M12 14a6 6 0 0 0-6 4M12 14a6 6 0 0 1 6 4"/>
  </svg>
);
const IconShare = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 16, height: 16 }}>
    <circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>
    <line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"/>
  </svg>
);
const IconBook = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 16, height: 16 }}>
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
  </svg>
);
const IconHome = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, color: 'var(--text-tertiary)' }}>
    <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>
  </svg>
);

export default function TopBar({ crumb }: TopBarProps) {
  const { selectedRepo, selectedBranch } = useRepoStore();
  const workspaceId = useWorkspaceStore((s) => s.workspaceId);
  const [liveAgents, setLiveAgents] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    function fetch() {
      getMcpAgents(workspaceId)
        .then((agents) => { if (!cancelled) setLiveAgents(agents.filter((a) => a.status === 'live').length); })
        .catch(() => {});
    }
    fetch();
    const id = setInterval(fetch, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [workspaceId]);

  const repoLabel = selectedRepo
    ? `${selectedRepo.display_name} · ${selectedBranch}`
    : 'No repo connected';

  return (
    <div className="topbar">
      <div className="crumb">
        <IconHome />
        <span className="sep">/</span>
        <span>Ask</span>
        <span className="sep">/</span>
        <span className="now">{crumb}</span>
      </div>
      <div className="tb-grow" />
      <div className="tb-chip">
        <IconGitBranch />
        <span className="mono" style={{ fontSize: 11 }}>{repoLabel}</span>
      </div>
      {liveAgents !== null && (
        <div className="tb-chip tb-chip--accent">
          <span className="dot dot--ok" />
          <span>{liveAgents} agent{liveAgents !== 1 ? 's' : ''} live</span>
        </div>
      )}
      <button className="icon-btn" title="Share"><IconShare /></button>
      <button className="icon-btn" title="Notes"><IconBook /></button>
    </div>
  );
}
