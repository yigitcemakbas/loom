import { useEffect, useState } from "react";

/** Ticks every second so "updated Ns ago" is genuinely live, not a static
 * string computed once at fetch time. Pass a TanStack Query result's
 * `dataUpdatedAt` (0 before the first successful fetch). */
export function useLastUpdated(dataUpdatedAt: number): string {
  const [, forceTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  if (!dataUpdatedAt) return "-";
  const seconds = Math.floor((Date.now() - dataUpdatedAt) / 1000);
  if (seconds < 2) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  return `${Math.floor(seconds / 60)}m ago`;
}
