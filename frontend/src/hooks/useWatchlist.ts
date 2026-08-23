import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addTickerToWatchlist,
  fetchWatchlistItems,
  fetchWatchlists,
  removeTickerFromWatchlist,
} from "../api/watchlists";

export function useWatchlists() {
  return useQuery({ queryKey: ["watchlists"], queryFn: fetchWatchlists });
}

export function useWatchlistItems(watchlistId: string | undefined) {
  return useQuery({
    queryKey: ["watchlist-items", watchlistId],
    queryFn: () => fetchWatchlistItems(watchlistId as string),
    enabled: Boolean(watchlistId),
  });
}

export function useAddTicker(watchlistId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ticker: string) => addTickerToWatchlist(watchlistId as string, ticker),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist-items", watchlistId] });
    },
  });
}

export function useRemoveTicker(watchlistId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (companyId: string) => removeTickerFromWatchlist(watchlistId as string, companyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchlist-items", watchlistId] });
    },
  });
}
