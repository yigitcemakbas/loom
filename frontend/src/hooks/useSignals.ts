import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  annotateSignal,
  dismissSignal,
  fetchSentimentSeries,
  fetchSignals,
  triggerAnalysis,
  type SignalFilters,
} from "../api/signals";

export function useSignals(filters: SignalFilters = {}) {
  return useQuery({
    queryKey: ["signals", filters],
    queryFn: () => fetchSignals(filters),
    // Analysis runs in the background, so poll to pick signals up as they land.
    refetchInterval: 30_000,
  });
}

export function useSentimentSeries(ticker: string | undefined) {
  return useQuery({
    queryKey: ["sentiment", ticker],
    queryFn: () => fetchSentimentSeries(ticker as string),
    enabled: Boolean(ticker),
    refetchInterval: 30_000,
  });
}

export function useTriggerAnalysis(ticker: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!ticker) return Promise.reject(new Error("No ticker selected."));
      return triggerAnalysis(ticker);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["signals"] });
      queryClient.invalidateQueries({ queryKey: ["sentiment", ticker] });
    },
  });
}

export function useAnnotateSignal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) => annotateSignal(id, note),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["signals"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export function useDismissSignal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: dismissSignal,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["signals"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}
