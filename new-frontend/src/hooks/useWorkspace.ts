import { useEffect } from 'react';
import { getMe, getRepos, getHealth } from '../data/brain_client';
import { flags } from '../data/feature_flags';
import { useWorkspaceStore } from '../store/workspace_store';
import { useRepoStore } from '../store/repo_store';

export function useWorkspaceBootstrap() {
  const setMe = useWorkspaceStore((s) => s.setMe);
  const setRepos = useRepoStore((s) => s.setRepos);

  useEffect(() => {
    // Auto-enable all live flags if the AI service is reachable
    getHealth()
      .then(() => flags.setAll(true))
      .catch(() => { /* stay in mock mode */ });

    // Load current user + workspace
    getMe().then(setMe).catch(() => {});

    // Load repos once we know the workspace_id
    getMe()
      .then((me) => getRepos(me.workspace_id).then(setRepos))
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
