import { useQuery } from "@tanstack/react-query";
import { fetchContradictions } from "../api/contradictions";

export function useContradictions(ticker: string | undefined) {
  return useQuery({
    queryKey: ["contradictions", ticker],
    queryFn: () => fetchContradictions(ticker as string),
    enabled: Boolean(ticker),
    staleTime: 10 * 60 * 1000,
    retry: false,
  });
}
