import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";

/** Polls the backend health endpoint so the top bar can show a real
 * connected/disconnected state instead of assuming the API is up. */
export function useApiHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data } = await apiClient.get<{ status: string }>("/health");
      return data.status === "ok";
    },
    refetchInterval: 15_000,
    retry: false,
  });
}
