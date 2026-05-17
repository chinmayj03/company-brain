import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getConversations, patchConversation } from '../data/brain_client';

export function useConversations(workspaceId: string, saved?: boolean) {
  return useQuery({
    queryKey: ['conversations', workspaceId, saved],
    queryFn:  () => getConversations(workspaceId, saved),
    enabled:  !!workspaceId,
  });
}

export function usePatchConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: { saved?: boolean; title?: string } }) =>
      patchConversation(id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['conversations'] }),
  });
}
