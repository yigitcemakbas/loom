import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deletePosition, fetchPortfolio, savePosition } from "../api/positions";
import type { PositionInput } from "../api/positions";
import { useAuth } from "../auth/AuthContext";

export function usePortfolio() {
  const { user } = useAuth();
  return useQuery({
    queryKey: ["portfolio"],
    queryFn: fetchPortfolio,
    enabled: Boolean(user),
    // Short: this view carries live prices and a P/L somebody is watching.
    staleTime: 30 * 1000,
  });
}

export function useSavePosition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ticker, input }: { ticker: string; input: PositionInput }) =>
      savePosition(ticker, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      // The change feed is filtered by what you hold, so it is stale the
      // moment the portfolio changes.
      queryClient.invalidateQueries({ queryKey: ["changes"] });
    },
  });
}

export function useDeletePosition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ticker: string) => deletePosition(ticker),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      queryClient.invalidateQueries({ queryKey: ["changes"] });
    },
  });
}

/** Which tickers this user holds or watches, for the views that reorder
 *  around them. Empty when signed out, which callers treat as "show
 *  everything" rather than as an empty page. */
export function useHeldTickers(): Set<string> {
  const { data } = usePortfolio();
  return new Set((data?.positions ?? []).map((p) => p.ticker));
}
