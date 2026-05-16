import { create } from 'zustand';
import type { MeResponse } from '../data/brain_client';

interface WorkspaceState {
  me: MeResponse | null;
  workspaceId: string;
  setMe: (me: MeResponse) => void;
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  me: null,
  workspaceId: '00000000-0000-0000-0000-000000000001',
  setMe: (me) => set({ me, workspaceId: me.workspace_id }),
}));
