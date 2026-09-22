import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchBrief, fetchBriefForHorizon, fetchBriefs, refreshBrief } from "../api/briefs";

export function useBriefs(limit?: number) {
  return useQuery({ queryKey: ["briefs", limit], queryFn: () => fetchBriefs(limit) });
}

export function useBrief(ticker: string | undefined) {
  return useQuery({
    queryKey: ["brief", ticker],
    queryFn: () => fetchBrief(ticker as string),
    enabled: Boolean(ticker),
  });
}

export function useRefreshBrief(ticker: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => refreshBrief(ticker as string),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["brief", ticker] });
      qc.invalidateQueries({ queryKey: ["briefs"] });
    },
  });
}

/** A company's read over one holding period.
 *
 *  Kept fresh for a long time because the inputs only change when new findings
 *  land, and a card that refetched on every hover would flicker between
 *  identical answers. */
export function useBriefHorizon(ticker: string, horizon: string | null) {
  return useQuery({
    queryKey: ["brief-horizon", ticker, horizon],
    queryFn: () => fetchBriefForHorizon(ticker, horizon as string),
    enabled: Boolean(ticker && horizon),
    staleTime: 5 * 60 * 1000,
  });
}
