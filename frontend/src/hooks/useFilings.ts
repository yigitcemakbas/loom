import { useQuery } from "@tanstack/react-query";
import {
  fetchDocuments,
  searchDocuments,
  type DocumentFilters,
  type SearchFilters,
} from "../api/documents";

export function useFilings(filters: DocumentFilters = {}) {
  return useQuery({
    queryKey: ["filings", filters],
    queryFn: () => fetchDocuments(filters),
  });
}

/** Full-text search over document content. Disabled below two characters,
 *  which matches the API's own minimum and avoids firing a request on the
 *  first keystroke of every query. */
export function useDocumentSearch(filters: SearchFilters) {
  const enabled = filters.q.trim().length >= 2;
  return useQuery({
    queryKey: ["document-search", filters],
    queryFn: () => searchDocuments({ ...filters, q: filters.q.trim() }),
    enabled,
  });
}
