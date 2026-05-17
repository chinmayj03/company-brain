import { useQuery } from '@tanstack/react-query';
import { getMcpAgents } from '../data/brain_client';

export function useMcpAgents(workspaceId: string) {
  return useQuery({
    queryKey:        ['agents', workspaceId],
    queryFn:         () => getMcpAgents(workspaceId),
    enabled:         !!workspaceId,
    refetchInterval: 5_000, // live roster — poll every 5s
  });
}
