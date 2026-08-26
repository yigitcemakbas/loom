import { useQuery } from "@tanstack/react-query";
import { fetchCompanyEarnings, fetchUpcomingEarnings } from "../api/earnings";

export function useUpcomingEarnings() {
  return useQuery({ queryKey: ["earnings", "upcoming"], queryFn: fetchUpcomingEarnings });
}

export function useCompanyEarnings(ticker: string | undefined) {
  return useQuery({
    queryKey: ["earnings", ticker],
    queryFn: () => fetchCompanyEarnings(ticker as string),
    enabled: Boolean(ticker),
  });
}
