import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { fetchPrices, fetchRanges } from "../api/prices";

/** Price data goes stale fast, but a rotating panel must not refetch on every
 *  pass; the server caches for a minute and the client matches it. */
export function usePrices(ticker: string | undefined, range: string) {
  return useQuery({
    queryKey: ["prices", ticker, range],
    queryFn: () => fetchPrices(ticker as string, range),
    enabled: Boolean(ticker),
    staleTime: 60_000,
    retry: false, // a ticker with no series (an ETF, a delisting) should skip, not retry
  });
}

export function useRanges() {
  return useQuery({ queryKey: ["price-ranges"], queryFn: fetchRanges, staleTime: Infinity });
}

/** Warm the cache for the ticker the rotation is about to show.
 *
 *  Without this the incoming panel slides in showing "Loading price…" and then
 *  pops to a chart, which is exactly the jarring switch the animation exists to
 *  remove. Prefetching one step ahead means the slide always carries content. */
export function usePrefetchPrices(ticker: string | undefined, range: string) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!ticker) return;
    qc.prefetchQuery({
      queryKey: ["prices", ticker, range],
      queryFn: () => fetchPrices(ticker, range),
      staleTime: 60_000,
    });
  }, [qc, ticker, range]);
}
