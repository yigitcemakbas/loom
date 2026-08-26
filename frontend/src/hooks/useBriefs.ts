import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchBrief, fetchBriefs, refreshBrief } from "../api/briefs";

export function useBriefs() {
  return useQuery({ queryKey: ["briefs"], queryFn: fetchBriefs });
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
