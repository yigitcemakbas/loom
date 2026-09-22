import { apiClient } from "./client";
import type { Portfolio, Position } from "../types/models";

export async function fetchPortfolio(): Promise<Portfolio> {
  const { data } = await apiClient.get<Portfolio>("/positions");
  return data;
}

export interface PositionInput {
  shares?: number | null;
  cost_basis?: number | null;
  note?: string | null;
}

export async function savePosition(ticker: string, input: PositionInput): Promise<Position> {
  const { data } = await apiClient.put<Position>(`/positions/${ticker}`, {
    ticker,
    ...input,
  });
  return data;
}

export async function deletePosition(ticker: string): Promise<void> {
  await apiClient.delete(`/positions/${ticker}`);
}
