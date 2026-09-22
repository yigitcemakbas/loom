import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useChanges } from "../../hooks/useChanges";
import type { Change } from "../../types/models";

const WINDOWS = [
  { days: 1, label: "24H" },
  { days: 7, label: "1W" },
  { days: 30, label: "1M" },
] as const;

/** What moved since you last looked.
 *
 *  The reason to open Loom rather than a thing you consult once you already
 *  suspect something. Until this existed Loom held a hundred and twenty-nine
 *  opinions and said nothing when one of them reversed, which made it a
 *  reference work: useful, and only if you remembered to check every company
 *  every week, which nobody does.
 *
 *  Verdict reversals are separated from everything else rather than sorted
 *  above it. Loom changing its mind about a company is a different kind of
 *  event from a filing matching a keyword, and mixing them in one list invites
 *  a reader to skim past the first while scanning the second. */
export function ChangeFeed() {
  const [days, setDays] = useState<number>(7);
  const { data, isLoading } = useChanges(days);

  const { verdicts, rest } = useMemo(() => {
    const all = data?.changes ?? [];
    return {
      verdicts: all.filter((c) => c.kind === "verdict"),
      rest: all.filter((c) => c.kind !== "verdict"),
    };
  }, [data]);

  if (isLoading) return null;

  const total = (data?.changes ?? []).length;

  return (
    <section className="change-feed">
      <header className="change-head">
        <h2>
          {total === 0
            ? "Nothing has moved"
            : `${total} ${total === 1 ? "thing" : "things"} moved`}
        </h2>
        <div className="horizon-row" style={{ margin: 0 }}>
          <span className="horizon-hint">in the last</span>
          {WINDOWS.map((w) => (
            <button
              key={w.days}
              className={`horizon-btn ${days === w.days ? "active" : ""}`}
              onClick={() => setDays(w.days)}
              aria-pressed={days === w.days}
            >
              {w.label}
            </button>
          ))}
        </div>
      </header>

      {total === 0 && (
        // A real answer, not an empty state. Loom checked and nothing crossed
        // its thresholds, which is different from Loom not having looked.
        <p className="change-quiet">
          Loom re-read its own verdicts, rescored the universe and matched every
          new filing against what it was already watching for. Nothing crossed
          the line worth interrupting you about.
        </p>
      )}

      {verdicts.length > 0 && (
        <div className="change-group">
          <h3>Loom changed its mind</h3>
          {verdicts.map((c) => <ChangeRow key={`${c.ticker}-${c.kind}`} change={c} prominent />)}
        </div>
      )}

      {rest.length > 0 && (
        <div className="change-group">
          <h3>Worth a look</h3>
          {rest.map((c, i) => <ChangeRow key={`${c.ticker}-${c.kind}-${i}`} change={c} />)}
        </div>
      )}
    </section>
  );
}

function ChangeRow({ change, prominent }: { change: Change; prominent?: boolean }) {
  return (
    <article className={`change-row ${change.kind} ${prominent ? "prominent" : ""}`}>
      <Link className="change-ticker" to={`/companies/${change.ticker}`}>
        {change.ticker}
      </Link>
      <div className="change-body">
        <p className="change-headline">{stripTicker(change.headline, change.ticker)}</p>
        <p className="change-detail">{change.detail}</p>
      </div>
      <span className="change-when">{when(change.occurred_at)}</span>
    </article>
  );
}

/** The ticker is already its own element on the row, so repeating it at the
 *  start of the sentence reads as a stutter. */
function stripTicker(headline: string, ticker: string): string {
  const prefix = `${ticker}: `;
  return headline.startsWith(prefix) ? headline.slice(prefix.length) : headline;
}

/** Relative time, coarse. "3 days ago" is what a reader needs; a timestamp to
 *  the second implies a precision that does not matter for a weekly rescore. */
function when(iso: string): string {
  const then = new Date(iso).getTime();
  const hours = (Date.now() - then) / 36e5;
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days}d ago`;
}
