import { apiClient } from "./client";
import type { PriceSeries } from "../types/models";

export async function fetchPrices(ticker: string, range: string): Promise<PriceSeries> {
  const { data } = await apiClient.get<PriceSeries>(`/companies/${ticker}/prices`, {
    params: { range },
  });
  return data;
}

export async function fetchRanges(): Promise<string[]> {
  const { data } = await apiClient.get<string[]>("/ranges");
  return data;
}
