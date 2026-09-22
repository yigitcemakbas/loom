import { useQuery } from "@tanstack/react-query";
import { fetchChanges } from "../api/changes";

export function useChanges(days = 7) {
  return useQuery({
    queryKey: ["changes", days],
    queryFn: () => fetchChanges(days),
    // Short, because this is the one view whose whole purpose is being
    // current. The scheduled jobs write into it while a tab is open.
    staleTime: 2 * 60 * 1000,
  });
}
