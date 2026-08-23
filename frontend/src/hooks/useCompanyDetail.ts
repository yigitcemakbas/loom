import { useQuery } from "@tanstack/react-query";
import { fetchCompany, fetchCompanyTimeline, fetchDocument } from "../api/companies";

export function useCompany(ticker: string | undefined) {
  return useQuery({
    queryKey: ["company", ticker],
    queryFn: () => fetchCompany(ticker as string),
    enabled: Boolean(ticker),
  });
}

export function useCompanyTimeline(ticker: string | undefined) {
  return useQuery({
    queryKey: ["company-timeline", ticker],
    queryFn: () => fetchCompanyTimeline(ticker as string),
    enabled: Boolean(ticker),
    // Near-live feel without websockets, per the plan's polling approach.
    refetchInterval: 60_000,
  });
}

export function useDocument(id: string | undefined) {
  return useQuery({
    queryKey: ["document", id],
    queryFn: () => fetchDocument(id as string),
    enabled: Boolean(id),
  });
}
