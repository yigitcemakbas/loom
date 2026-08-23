import { apiClient } from "./client";
import type { Company, RawDocument, RawDocumentDetail } from "../types/models";

export async function fetchCompany(ticker: string): Promise<Company> {
  const { data } = await apiClient.get<Company>(`/companies/${ticker}`);
  return data;
}

export async function fetchCompanyTimeline(ticker: string): Promise<RawDocument[]> {
  const { data } = await apiClient.get<RawDocument[]>(`/companies/${ticker}/timeline`);
  return data;
}

export async function fetchDocument(id: string): Promise<RawDocumentDetail> {
  const { data } = await apiClient.get<RawDocumentDetail>(`/documents/${id}`);
  return data;
}

export async function searchCompanies(query: string): Promise<Company[]> {
  const { data } = await apiClient.get<Company[]>("/companies", { params: { q: query } });
  return data;
}
