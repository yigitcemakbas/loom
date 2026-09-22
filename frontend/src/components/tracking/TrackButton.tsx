import { useDeletePosition, useHeldTickers, useSavePosition } from "../../hooks/usePortfolio";

/** Star a company so Loom knows it is one of yours.
 *
 *  Deliberately a small control rather than a prominent one. Tracking is a
 *  quiet, reversible preference, and a large button invites a reader to think
 *  it does more than it does: it changes what Loom shows first, not what Loom
 *  ingests or analyses. */
export function TrackButton({ ticker, compact }: { ticker: string; compact?: boolean }) {
  const followed = useHeldTickers();
  const save = useSavePosition();
  const remove = useDeletePosition();
  const tracked = followed.has(ticker);
  const pending = save.isPending || remove.isPending;

  // Following adds a position with no size, which is exactly what a watch is.
  // Keeping them one object means a company never has to be added twice, once
  // to watch and again to own.
  const toggle = () =>
    tracked ? remove.mutate(ticker) : save.mutate({ ticker, input: {} });

  return (
    <button
      className={`track-btn ${tracked ? "on" : ""} ${compact ? "compact" : ""}`}
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggle(); }}
      disabled={pending}
      aria-pressed={tracked}
      title={tracked ? `Stop following ${ticker}` : `Follow ${ticker}`}
    >
      {tracked ? "★" : "☆"}
      {!compact && <span>{tracked ? "following" : "follow"}</span>}
    </button>
  );
}
