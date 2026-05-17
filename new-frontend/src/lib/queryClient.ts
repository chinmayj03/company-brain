import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime:   30_000,   // 30s — data stays fresh; no refetch on every mount
      gcTime:      300_000,  // 5min — keep in cache
      retry:       1,
      refetchOnWindowFocus: false,
    },
  },
});
