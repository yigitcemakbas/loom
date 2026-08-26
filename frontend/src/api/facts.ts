import { apiClient } from "./client";
import type { FactType, InsiderActivitySummary, StructuredFact } from "../types/models";

export interface FactFilters {
  fact_type?: FactType;
  days?: number;
  limit?: number;
}

export async function fetchFacts(ticker: string, filters: FactFilters = {}): Promise<StructuredFact[]> {
  const { data } = await apiClient.get<StructuredFact[]>(`/companies/${ticker}/facts`, {
    params: filters,
  });
  return data;
}

export async function fetchInsiderActivity(
  ticker: string,
  days?: number,
): Promise<InsiderActivitySummary> {
  const { data } = await apiClient.get<InsiderActivitySummary>(
    `/companies/${ticker}/insider-activity`,
    { params: { days } },
  );
  return data;
}
