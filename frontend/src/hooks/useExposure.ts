import { useQuery } from "@tanstack/react-query";
import { fetchExposure } from "../api/exposure";

// The graph changes only when the exposure job reruns, which is daily at most,
// so it is cached hard: relaying it out on every visit would make the map
// appear to rearrange itself for no reason.
export function useExposure() {
  return useQuery({
    queryKey: ["exposure"],
    queryFn: fetchExposure,
    staleTime: 60 * 60 * 1000,
  });
}
