import { apiClient } from "./client";
import type { Brief } from "../types/models";

export async function fetchBriefs(limit?: number): Promise<Brief[]> {
  const { data } = await apiClient.get<Brief[]>("/briefs", {
    params: limit ? { limit } : undefined,
  });
  return data;
}

export async function fetchBrief(ticker: string): Promise<Brief> {
  const { data } = await apiClient.get<Brief>(`/companies/${ticker}/brief`);
  return data;
}

export async function refreshBrief(ticker: string): Promise<Brief> {
  const { data } = await apiClient.post<Brief>(`/companies/${ticker}/brief/refresh`);
  return data;
}

/** The same company judged over a stated holding period.
 *
 *  Not stored server-side: a brief is arithmetic over findings already held,
 *  so each horizon is computed on request rather than cached as a fourth thing
 *  to keep fresh. */
export async function fetchBriefForHorizon(
  ticker: string,
  horizon: string,
): Promise<Brief> {
  const { data } = await apiClient.get<Brief>(
    `/companies/${ticker}/brief/horizon`,
    { params: { horizon } },
  );
  return data;
}
