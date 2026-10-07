import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createApiKey, fetchApiKeys, revokeApiKey } from "../api/keys";
import { useAuth } from "../auth/AuthContext";

const KEY = ["api-keys"];

export function useApiKeys() {
  const { user } = useAuth();
  return useQuery({ queryKey: KEY, queryFn: fetchApiKeys, enabled: Boolean(user) });
}

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => createApiKey(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useRevokeApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => revokeApiKey(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
