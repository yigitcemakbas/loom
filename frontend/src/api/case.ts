import { apiClient } from "./client";
import type { CaseFile } from "../types/models";

export async function fetchCase(ticker: string): Promise<CaseFile> {
  const { data } = await apiClient.get<CaseFile>(`/companies/${ticker}/case`);
  return data;
}
