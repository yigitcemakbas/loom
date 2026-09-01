import { useQuery } from "@tanstack/react-query";
import { fetchTape } from "../api/tape";

// The tape is the most visible thing on the page and the cheapest to rebuild,
// so it refreshes on its own rather than waiting for a reload. Two minutes is
// well inside the cadence at which news actually lands.
const REFRESH_MS = 120_000;

export function useTape() {
  return useQuery({
    queryKey: ["tape"],
    queryFn: fetchTape,
    refetchInterval: REFRESH_MS,
    staleTime: REFRESH_MS,
  });
}
