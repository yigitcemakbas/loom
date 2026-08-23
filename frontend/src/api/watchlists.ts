import { apiClient } from "./client";
import type { Company, Watchlist } from "../types/models";

export async function fetchWatchlists(): Promise<Watchlist[]> {
  const { data } = await apiClient.get<Watchlist[]>("/watchlists");
  return data;
}

export async function fetchWatchlistItems(watchlistId: string): Promise<Company[]> {
  const { data } = await apiClient.get<Company[]>(`/watchlists/${watchlistId}/items`);
  return data;
}

export async function addTickerToWatchlist(
  watchlistId: string,
  ticker: string,
): Promise<Company[]> {
  const { data } = await apiClient.post<Company[]>(`/watchlists/${watchlistId}/items`, { ticker });
  return data;
}

export async function removeTickerFromWatchlist(
  watchlistId: string,
  companyId: string,
): Promise<Company[]> {
  const { data } = await apiClient.delete<Company[]>(
    `/watchlists/${watchlistId}/items/${companyId}`,
  );
  return data;
}
