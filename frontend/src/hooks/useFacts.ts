import { useQuery } from "@tanstack/react-query";
import { fetchFacts, fetchInsiderActivity, type FactFilters } from "../api/facts";

export function useFacts(ticker: string, filters: FactFilters = {}) {
  return useQuery({
    queryKey: ["facts", ticker, filters],
    queryFn: () => fetchFacts(ticker, filters),
    enabled: Boolean(ticker),
  });
}

export function useInsiderActivity(ticker: string, days?: number) {
  return useQuery({
    queryKey: ["insider-activity", ticker, days],
    queryFn: () => fetchInsiderActivity(ticker, days),
    enabled: Boolean(ticker),
  });
}
