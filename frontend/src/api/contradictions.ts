import { apiClient } from "./client";
import type { Contradictions } from "../types/models";

export async function fetchContradictions(ticker: string): Promise<Contradictions> {
  const { data } = await apiClient.get<Contradictions>(`/companies/${ticker}/contradictions`);
  return data;
}
