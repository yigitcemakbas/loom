import { apiClient } from "./client";
import type { Brief } from "../types/models";

export async function fetchBriefs(): Promise<Brief[]> {
  const { data } = await apiClient.get<Brief[]>("/briefs");
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
