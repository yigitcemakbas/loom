import { apiClient } from "./client";
import type { CompanyFactors, FactorLeaderboardRow } from "../types/models";

export async function fetchCompanyFactors(ticker: string): Promise<CompanyFactors> {
  const { data } = await apiClient.get<CompanyFactors>(`/companies/${ticker}/factors`);
  return data;
}

export async function fetchFactorLeaderboard(): Promise<FactorLeaderboardRow[]> {
  const { data } = await apiClient.get<FactorLeaderboardRow[]>("/factors/leaderboard");
  return data;
}
