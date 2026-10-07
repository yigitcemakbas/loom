import { useState } from "react";
import { AddTickerForm } from "./AddTickerForm";
import { useAddTicker, useWatchlists } from "../../hooks/useWatchlist";
import { useAuth } from "../../auth/AuthContext";

/** Introducing a company Loom has never read.
 *
 *  Distinct from the position form beside it, and the distinction is the whole
 *  reason this exists. Saving a position records what you own and rejects
 *  anything Loom does not already track; this resolves a symbol against SEC's
 *  company directory, creates it, and queues the first ingest. Until it was
 *  wired there was no path in the interface to a company outside the set
 *  already in the database, which made the universe fixed from a reader's side
 *  however many tickers SEC publishes.
 *
 *  The two stay separate rather than one form guessing: adding to Loom spends
 *  minutes of SEC requests and model quota, and a reader should be choosing
 *  that deliberately rather than discovering it as a side effect of typing a
 *  symbol into a portfolio row. */
export function AddCompany() {
  const { user } = useAuth();
  const { data: watchlists, isLoading: loadingLists } = useWatchlists();
  const watchlistId = watchlists?.[0]?.id;
  const add = useAddTicker(watchlistId);
  const [note, setNote] = useState<string | null>(null);

  // Signed out, this control would only ever produce a 401. Saying so is more
  // use than a button that fails on press.
  if (!user) {
    return (
      <p className="empty-state" style={{ padding: "10px 0" }}>
        Sign in to add a company. Reading what Loom already holds needs no account.
      </p>
    );
  }

  function submit(ticker: string) {
    setNote(null);
    add.mutate(ticker, {
      onSuccess: () => {
        setNote(
          `${ticker} added. Loom is retrieving its filings, transcripts, insider ` +
          `records and prices now; a full history takes several minutes and the ` +
          `pages fill in as records arrive.`,
        );
      },
      onError: (error: unknown) => setNote(explain(ticker, error)),
    });
  }

  return (
    <div style={{ marginTop: 14 }}>
      {/* .section-label, not .sidebar-section. The latter is scoped to the
          chrome and paints --on-chrome, so on this page it was cream type on
          the cream page at 1.00:1 — the heading was simply not there. */}
      <div className="section-label" style={{ padding: "0 0 2px" }}>
        Add a company Loom has not read
      </div>
      <AddTickerForm
        onAdd={submit}
        disabled={add.isPending || loadingLists || !watchlistId}
        label={add.isPending ? "Adding…" : loadingLists ? "Loading…" : "Add company"}
      />
      {note && (
        <p className="empty-state enter" style={{ padding: "8px 0 0" }} role="status">
          {note}
        </p>
      )}
    </div>
  );
}

/** Turn a failure into something the reader can act on.
 *
 *  The three cases need different responses and are easy to confuse: a symbol
 *  that does not exist is the reader's typo, an unreachable SEC is nothing to
 *  do with them and worth waiting out, and an expired session needs a sign-in.
 *  The backend already separates them, 404 from 503 from 401, so collapsing
 *  them back into "something went wrong" would discard work already done. */
function explain(ticker: string, error: unknown): string {
  const status = (error as { response?: { status?: number } })?.response?.status;
  const detail = (error as { response?: { data?: { detail?: string } } })
    ?.response?.data?.detail;

  if (status === 404) {
    return `${ticker} is not a symbol SEC recognises. Check the spelling; Loom
      tracks US filers, so a foreign listing or a fund may legitimately be absent.`
      .replace(/\s+/g, " ");
  }
  if (status === 503) {
    return detail ?? "SEC is unreachable right now, so the symbol could not be resolved. Nothing is wrong with the ticker; try again shortly.";
  }
  if (status === 401) {
    return "Your session has expired. Sign in again and the company will be added.";
  }
  return detail ?? `${ticker} could not be added.`;
}
