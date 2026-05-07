import { create } from 'zustand';

/**
 * Global app store — persists state across tab/route switches.
 * Prevents form inputs from being lost when navigating between pages.
 */

export const usePipelineStore = create((set) => ({
  // API Explorer form state.
  // Branch is now stored per-repo (in repos[].branch) — no global branch field.
  endpoint: '',
  method: 'POST',
  repos: [{ url: '', type: 'backend', branch: 'main' }],
  jobId: null,

  setEndpoint: (endpoint) => set({ endpoint }),
  setMethod:   (method)   => set({ method }),
  setRepos:    (repos)    => set({ repos }),
  setJobId:    (jobId)    => set({ jobId }),

  addRepo: () =>
    set((s) => ({
      repos: [...s.repos, { url: '', type: 'frontend', branch: 'main' }],
    })),

  removeRepo: (i) =>
    set((s) => ({ repos: s.repos.filter((_, idx) => idx !== i) })),

  updateRepo: (i, field, val) =>
    set((s) => ({
      repos: s.repos.map((r, idx) => (idx === i ? { ...r, [field]: val } : r)),
    })),
}));
