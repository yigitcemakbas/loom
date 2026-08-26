import { apiClient } from "./client";
import type { EarningsOutlook } from "../types/models";

export async function fetchUpcomingEarnings(): Promise<EarningsOutlook[]> {
  const { data } = await apiClient.get<EarningsOutlook[]>("/earnings/upcoming");
  return data;
}

export async function fetchCompanyEarnings(ticker: string): Promise<EarningsOutlook> {
  const { data } = await apiClient.get<EarningsOutlook>(`/companies/${ticker}/earnings`);
  return data;
}
