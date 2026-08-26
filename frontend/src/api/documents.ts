import { apiClient } from "./client";
import type { RawDocumentWithContext, SearchHit } from "../types/models";

export interface DocumentFilters {
  ticker?: string;
  doc_subtype?: string;
  limit?: number;
}

export async function fetchDocuments(filters: DocumentFilters = {}): Promise<RawDocumentWithContext[]> {
  const { data } = await apiClient.get<RawDocumentWithContext[]>("/documents", { params: filters });
  return data;
}

export interface SearchFilters extends DocumentFilters {
  q: string;
}

export async function searchDocuments(filters: SearchFilters): Promise<SearchHit[]> {
  const { data } = await apiClient.get<SearchHit[]>("/documents/search", { params: filters });
  return data;
}
