import { useQuery } from "@tanstack/react-query";
import { fetchCompanyFactors, fetchFactorLeaderboard } from "../api/factors";

// Scores change only when the scoring job runs, which is on the cadence of
// filings rather than of page views, so this is cached hard.
const HOUR = 60 * 60 * 1000;

export function useCompanyFactors(ticker: string | undefined) {
  return useQuery({
    queryKey: ["factors", ticker],
    queryFn: () => fetchCompanyFactors(ticker as string),
    enabled: Boolean(ticker),
    staleTime: HOUR,
    // A company Loom has not filed enough about returns 404, which is an
    // answer rather than a failure. Retrying it just delays the empty state.
    retry: false,
  });
}

export function useFactorLeaderboard() {
  return useQuery({
    queryKey: ["factors", "leaderboard"],
    queryFn: fetchFactorLeaderboard,
    staleTime: HOUR,
  });
}
