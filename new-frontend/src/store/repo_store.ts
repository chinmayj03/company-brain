import { create } from 'zustand';
import type { Repo } from '../data/brain_client';

interface RepoState {
  repos: Repo[];
  selectedRepo: Repo | null;
  selectedBranch: string;
  setRepos: (repos: Repo[]) => void;
  selectRepo: (repo: Repo) => void;
  selectBranch: (branch: string) => void;
}

export const useRepoStore = create<RepoState>((set) => ({
  repos: [],
  selectedRepo: null,
  selectedBranch: 'main',
  setRepos: (repos) => set({
    repos,
    selectedRepo: repos[0] ?? null,
    selectedBranch: repos[0]?.current_branch ?? 'main',
  }),
  selectRepo: (repo) => set({ selectedRepo: repo, selectedBranch: repo.current_branch }),
  selectBranch: (branch) => set({ selectedBranch: branch }),
}));
