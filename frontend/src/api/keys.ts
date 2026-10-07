import { apiClient } from "./client";

export interface ApiKey {
  id: string;
  name: string;
  /** The leading characters only. Enough to match a key in a config file
   *  against this row, far too little to authenticate with. */
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** The one response that carries the key itself. The server stores a digest
 *  and cannot produce the plaintext again. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export async function fetchApiKeys(): Promise<ApiKey[]> {
  const { data } = await apiClient.get<ApiKey[]>("/auth/api-keys");
  return data;
}

export async function createApiKey(name: string): Promise<ApiKeyCreated> {
  const { data } = await apiClient.post<ApiKeyCreated>("/auth/api-keys", { name });
  return data;
}

export async function revokeApiKey(id: string): Promise<void> {
  await apiClient.delete(`/auth/api-keys/${id}`);
}
