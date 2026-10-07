import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useUpcomingEarnings } from "../hooks/useEarnings";
import { useDashboard } from "../hooks/useDashboard";
import { useHeldTickers } from "../hooks/usePortfolio";
import type { EarningsOutlook } from "../types/models";

/** When everything reports.
 *
 *  Loom has held scheduled dates for the whole tracked universe since the
 *  earnings facts landed, and showed them in two places: a banner counting the
 *  companies you follow reporting inside a fortnight, and a tape item for the
 *  very next one. Both answer "is anything imminent". Neither answers "what
 *  does the next two months look like", which is the question someone asks
 *  when deciding what to read this week.
 *
 *  Results move prices more than anything else Loom watches, and they are the
 *  one thing in this database whose timing is known in advance. A page that
 *  does nothing but lay that out in order is worth more than it costs. */

const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function dayLabel(iso: string, daysUntil: number): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  const when = `${DAY[d.getDay()]} ${d.getDate()} ${MONTH[d.getMonth()]}`;
  if (daysUntil === 0) return `${when} · today`;
  if (daysUntil === 1) return `${when} · tomorrow`;
  return when;
}

const eps = (v: number | null) => (v === null ? "—" : v.toFixed(2));

type Scope = "all" | "mine";

export function CalendarPage() {
  const earnings = useUpcomingEarnings();
  const dashboard = useDashboard();
  const tracked = useHeldTickers();
  const [scope, setScope] = useState<Scope>("all");

  const nameOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of dashboard.data?.companies ?? []) map.set(row.ticker, row.name);
    return (ticker: string) => map.get(ticker) ?? "";
  }, [dashboard.data]);

  const all = earnings.data ?? [];

  // A company with no scheduled date is not a company reporting today, and
  // sorting nulls to the end would put them at the bottom of a calendar as if
  // they were the furthest away. They are counted instead.
  const dated = useMemo(
    () => all.filter((e) => e.next_date !== null && e.days_until !== null && e.days_until >= 0),
    [all],
  );
  const undated = all.length - dated.length;

  const mine = useMemo(() => dated.filter((e) => tracked.has(e.ticker)), [dated, tracked]);
  const shown = scope === "mine" ? mine : dated;

  /** Grouped by the day they report, in order. The date is a heading rather
   *  than a repeated column: twenty rows carrying the same date is twenty
   *  chances to misread which day a row belongs to. */
  const days = useMemo(() => {
    const groups = new Map<string, EarningsOutlook[]>();
    for (const e of shown) {
      const key = e.next_date as string;
      const list = groups.get(key);
      if (list) list.push(e);
      else groups.set(key, [e]);
    }
    return [...groups.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([date, items]) => ({
        date,
        daysUntil: items[0].days_until as number,
        items: items.sort((a, b) => a.ticker.localeCompare(b.ticker)),
      }));
  }, [shown]);

  if (earnings.isLoading) return <p className="empty-state">Loading the calendar…</p>;

  const thisWeek = dated.filter((e) => (e.days_until as number) <= 7).length;
  const mineThisWeek = mine.filter((e) => (e.days_until as number) <= 7).length;

  return (
    <div>
      <header className="today-head">
        <h1>Earnings calendar</h1>
        <p className="today-sub">
          Every scheduled report Loom knows about, soonest first.{" "}
          {thisWeek > 0
            ? `${thisWeek} ${thisWeek === 1 ? "company reports" : "companies report"} in the next seven days, ${mineThisWeek} of them yours.`
            : "Nothing is scheduled in the next seven days."}{" "}
          Results move prices more than anything else Loom watches, and they are
          the only thing here whose timing is known in advance.
        </p>
      </header>

      <div className="grid grid-main-side">
        <div className="panel panel-body-flush">
          <div className="panel-head">
            <span className="panel-title">
              {scope === "mine" ? "Yours" : "All tracked"} · {shown.length}
            </span>
            <div className="scope-switch">
              <button
                className={`horizon-btn ${scope === "all" ? "active" : ""}`}
                onClick={() => setScope("all")}
                aria-pressed={scope === "all"}
              >
                ALL
              </button>
              <button
                className={`horizon-btn ${scope === "mine" ? "active" : ""}`}
                onClick={() => setScope("mine")}
                aria-pressed={scope === "mine"}
                disabled={mine.length === 0}
              >
                YOURS
              </button>
            </div>
          </div>

          {days.length === 0 ? (
            <p className="empty-state" style={{ padding: "12px 10px" }}>
              {scope === "mine"
                ? "None of the companies you follow have a scheduled date yet."
                : "No scheduled dates. Earnings dates arrive with the earnings facts; they are absent until an ingest has run."}
            </p>
          ) : (
            <table className="data-table calendar-table">
              <thead>
                <tr>
                  <th>Tkr</th>
                  <th>Company</th>
                  <th>Quarter</th>
                  <th>When</th>
                  <th className="num">EPS est.</th>
                  <th className="num">In</th>
                </tr>
              </thead>
              <tbody>
                {days.map((day) => (
                  // Fragment with the key, not the first row: a keyed row
                  // inside an unkeyed fragment gives React nothing stable to
                  // reconcile the group against when the filter changes.
                  <Fragment key={day.date}>
                    <tr className="calendar-day">
                      <td colSpan={5}>{dayLabel(day.date, day.daysUntil)}</td>
                      <td className="num">{day.items.length}</td>
                    </tr>
                    {day.items.map((e) => {
                      const ours = tracked.has(e.ticker);
                      return (
                        <tr key={`${day.date}-${e.ticker}`} className={ours ? "is-yours" : undefined}>
                          <td>
                            <Link className="ticker-symbol" to={`/companies/${e.ticker}`}>
                              {e.ticker}
                            </Link>
                          </td>
                          <td className="calendar-name">{nameOf(e.ticker)}</td>
                          <td className="dim">{e.quarter_label ?? "—"}</td>
                          {/* Before the open or after the close changes whether
                              a result lands inside a session, so it is worth a
                              column rather than a tooltip. */}
                          <td className="dim">{e.when_label ?? "—"}</td>
                          <td className="num mono">{eps(e.eps_estimate)}</td>
                          <td className="num mono dim">
                            {e.days_until === 0 ? "today" : `${e.days_until}d`}
                          </td>
                        </tr>
                      );
                    })}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="stack">
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Yours, next</span>
              <span className="faint" style={{ fontSize: 9 }}>{mine.length}</span>
            </div>
            {mine.length === 0 ? (
              <p className="empty-state" style={{ padding: "8px 10px" }}>
                Nothing scheduled for the companies you follow.{" "}
                <Link to="/portfolio">Add one →</Link>
              </p>
            ) : (
              <table className="data-table">
                <tbody>
                  {mine.slice(0, 8).map((e) => (
                    <tr key={e.ticker}>
                      <td>
                        <Link className="ticker-symbol" to={`/companies/${e.ticker}`}>{e.ticker}</Link>
                      </td>
                      <td className="dim">{e.quarter_label ?? "—"}</td>
                      <td className="num mono">
                        {e.days_until === 0 ? "today" : `${e.days_until}d`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="panel">
            <div className="panel-head"><span className="panel-title">What this is</span></div>
            <p className="panel-note">
              Dates and consensus come from the earnings provider, not from
              Loom's own reading, and a scheduled date is an announcement rather
              than a commitment: companies move them. The EPS estimate is the
              provider's consensus and is shown so a result can be read against
              something, not because Loom has an opinion about it.
            </p>
            {undated > 0 && (
              <p className="panel-note">
                {undated} tracked {undated === 1 ? "company has" : "companies have"} no
                scheduled date on file. Absent from this page rather than sorted to the
                bottom, which would read as reporting furthest away.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
