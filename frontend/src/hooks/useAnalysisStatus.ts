import { useQuery } from "@tanstack/react-query";
import { fetchSystemStatus } from "../api/status";

export function useSystemStatus() {
  return useQuery({
    queryKey: ["status"],
    queryFn: fetchSystemStatus,
    refetchInterval: 30_000,
  });
}
