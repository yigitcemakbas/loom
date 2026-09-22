import { apiClient } from "./client";
import type { CompanyPrior, PriorCoverageRow } from "../types/models";

export async function fetchPriorCoverage(): Promise<PriorCoverageRow[]> {
  const { data } = await apiClient.get<PriorCoverageRow[]>("/priors");
  return data;
}

export async function fetchCompanyPrior(ticker: string): Promise<CompanyPrior> {
  const { data } = await apiClient.get<CompanyPrior>(`/companies/${ticker}/prior`);
  return data;
}
