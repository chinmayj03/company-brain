import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getSources, triggerSync, registerSource, deleteSource } from '../data/brain_client';

export function useSources(workspaceId: string) {
  return useQuery({
    queryKey: ['sources', workspaceId],
    queryFn:  () => getSources(workspaceId),
    enabled:  !!workspaceId,
  });
}

export function useTriggerSync(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) => triggerSync(workspaceId, sourceId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sources', workspaceId] }),
  });
}

export function useRegisterSource(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof registerSource>[1]) =>
      registerSource(workspaceId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sources', workspaceId] }),
  });
}

export function useDeleteSource(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) => deleteSource(workspaceId, sourceId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sources', workspaceId] }),
  });
}
