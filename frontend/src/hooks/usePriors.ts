import { useQuery } from "@tanstack/react-query";
import { fetchCompanyPrior, fetchPriorCoverage } from "../api/priors";

const TEN_MINUTES = 10 * 60 * 1000;

export function usePriorCoverage() {
  return useQuery({
    queryKey: ["priors"],
    queryFn: fetchPriorCoverage,
    staleTime: TEN_MINUTES,
  });
}

export function useCompanyPrior(ticker: string | undefined) {
  return useQuery({
    queryKey: ["prior", ticker],
    queryFn: () => fetchCompanyPrior(ticker as string),
    enabled: Boolean(ticker),
    staleTime: TEN_MINUTES,
    // A company with no prior returns 404, which is an answer. Retrying it
    // only delays the empty state that explains the gap.
    retry: false,
  });
}
