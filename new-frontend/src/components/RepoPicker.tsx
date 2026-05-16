import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRepoStore } from '../store/repo_store';
import { getBranches } from '../data/brain_client';
import { useWorkspaceStore } from '../store/workspace_store';

const IconGit2 = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
    <circle cx="12" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>
    <path d="M12 8v6"/><path d="M12 14a6 6 0 0 0-6 4M12 14a6 6 0 0 1 6 4"/>
  </svg>
);
const IconChevronDown = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ width: 11, height: 11, transform: 'rotate(90deg)' }}>
    <polyline points="9 18 15 12 9 6"/>
  </svg>
);

export default function RepoPicker() {
  const { repos, selectedRepo, selectedBranch, selectRepo, selectBranch } = useRepoStore();
  const workspaceId = useWorkspaceStore((s) => s.workspaceId);
  const navigate = useNavigate();

  const [open, setOpen] = useState(false);
  const [branches, setBranches] = useState<string[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Load branches when dropdown opens
  useEffect(() => {
    if (open && selectedRepo) {
      getBranches(workspaceId, selectedRepo.id)
        .then((b) => setBranches(b.branches))
        .catch(() => setBranches([selectedRepo.current_branch]));
    }
  }, [open, selectedRepo, workspaceId]);

  if (repos.length === 0) {
    return (
      <span
        className="scope"
        style={{ cursor: 'pointer', color: 'var(--accent-primary)' }}
        onClick={() => navigate('/sources')}
      >
        <IconGit2 /> Connect a repo →
      </span>
    );
  }

  if (repos.length === 1 && selectedRepo) {
    return (
      <span className="scope">
        <IconGit2 /> {selectedRepo.display_name}@{selectedBranch}
      </span>
    );
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <span
        className="scope"
        style={{ cursor: 'pointer' }}
        onClick={() => setOpen((o) => !o)}
      >
        <IconGit2 />
        {selectedRepo ? `${selectedRepo.display_name}@${selectedBranch}` : 'Select repo'}
        <IconChevronDown />
      </span>

      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 1000,
          background: 'var(--bg-overlay, #1A2030)',
          border: '1px solid var(--border-default, #2F394A)',
          borderRadius: 8, display: 'flex', minWidth: 340,
          boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
          fontFamily: 'var(--font-sans)',
        }}>
          {/* Repo list */}
          <div style={{ flex: 1, padding: 8, borderRight: '1px solid var(--border-default, #2F394A)' }}>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', padding: '2px 8px 6px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Repositories</div>
            {repos.map((r) => (
              <div
                key={r.id}
                onClick={() => { selectRepo(r); setBranches([]); }}
                style={{
                  padding: '6px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
                  background: selectedRepo?.id === r.id ? 'var(--bg-surface, #20293A)' : 'transparent',
                  color: selectedRepo?.id === r.id ? 'var(--text-primary)' : 'var(--text-secondary)',
                }}
              >
                <div style={{ fontWeight: 500 }}>{r.display_name}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 1 }}>
                  {r.sync_status === 'ok' ? '✓' : r.sync_status === 'error' ? '✗' : '…'} {r.repo_path.split('/').slice(-2).join('/')}
                </div>
              </div>
            ))}
          </div>

          {/* Branch list */}
          <div style={{ flex: 1, padding: 8 }}>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', padding: '2px 8px 6px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Branch</div>
            {(branches.length ? branches : [selectedRepo?.current_branch ?? 'main']).map((b) => (
              <div
                key={b}
                onClick={() => { selectBranch(b); setOpen(false); }}
                style={{
                  padding: '6px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
                  background: selectedBranch === b ? 'var(--bg-surface, #20293A)' : 'transparent',
                  color: selectedBranch === b ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontFamily: 'var(--font-mono)',
                }}
              >
                {b}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
