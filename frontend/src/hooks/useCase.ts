import { useQuery } from "@tanstack/react-query";
import { fetchCase } from "../api/case";

export function useCase(ticker: string | undefined) {
  return useQuery({
    queryKey: ["case", ticker],
    queryFn: () => fetchCase(ticker as string),
    enabled: Boolean(ticker),
    staleTime: 5 * 60 * 1000,
  });
}
