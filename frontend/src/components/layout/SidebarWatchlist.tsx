import { NavLink } from "react-router-dom";
import { usePortfolio } from "../../hooks/usePortfolio";
import { useUpcomingEarnings } from "../../hooks/useEarnings";
import type { Position, Stance } from "../../types/models";

/** The companies you follow, live, in the rail.
 *
 *  The sidebar was nine links and roughly nine hundred vertical pixels of
 *  nothing, in a product whose stated design constraint is that a large screen
 *  showing a narrow column is the failure to avoid. The fix is not more
 *  navigation — every page this app has is already linked, and a tenth
 *  destination makes the rail longer to scan without adding anywhere to go.
 *  What a terminal's rail carries is state you want in view no matter which
 *  page you are on.
 *
 *  So this is the watchlist: price, the 24h move, and Loom's standing read on
 *  each name, visible from the filings page, the risk page and everywhere
 *  else. It is one request, the same one the portfolio page makes, so the data
 *  is shared rather than fetched twice. */

const tone = (stance: Stance | null): string => {
  if (!stance) return "";
  if (stance.includes("positive")) return "is-positive";
  if (stance.includes("negative")) return "is-negative";
  if (stance === "mixed") return "is-mixed";
  return "";
};

const price = (v: number | null) =>
  v === null ? "—" : v >= 1000 ? v.toFixed(0) : v.toFixed(2);

const move = (v: number | null) => (v === null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(2)}`);

const moveClass = (v: number | null) =>
  v === null || v === 0 ? "dim" : v > 0 ? "value-positive" : "value-negative";

/** Held first, then watched, each alphabetically. Ownership is the stronger
 *  claim on attention and the order should say so without a second column. */
function order(positions: Position[]): Position[] {
  return [...positions].sort(
    (a, b) =>
      Number(b.is_held) - Number(a.is_held) || a.ticker.localeCompare(b.ticker),
  );
}

export function SidebarWatchlist() {
  const portfolio = usePortfolio();
  const earnings = useUpcomingEarnings();

  const positions = portfolio.data?.positions ?? [];

  // Nothing followed yet. The rail is the most persistent surface in the app,
  // so this is the right place to say what is missing — but once, quietly,
  // not as a panel competing with the page.
  if (!portfolio.isLoading && positions.length === 0) {
    return (
      <div className="rail-block">
        <div className="rail-head"><span>Watching</span></div>
        <NavLink to="/portfolio" className="rail-empty">
          Nothing followed yet. Add a company →
        </NavLink>
      </div>
    );
  }

  const rows = order(positions);
  const held = rows.filter((p) => p.is_held).length;

  // Only companies you follow, only the ones close enough to plan around.
  // Three, because the rail is a glance and a fourth line is a list.
  const followed = new Set(rows.map((p) => p.ticker));
  const reporting = (earnings.data ?? [])
    .filter((e) => followed.has(e.ticker) && e.days_until !== null && e.days_until <= 21)
    .sort((a, b) => (a.days_until as number) - (b.days_until as number))
    .slice(0, 3);

  return (
    <>
      <div className="rail-block">
        <div className="rail-head">
          <span>Watching</span>
          <span className="rail-count mono">
            {held > 0 ? `${held} held · ${rows.length}` : rows.length}
          </span>
        </div>

        <div className="rail-rows">
          {rows.map((p) => (
            <NavLink
              key={p.ticker}
              to={`/companies/${p.ticker}`}
              className={({ isActive }) =>
                `rail-row ${tone(p.stance)}${isActive ? " active" : ""}`
              }
              title={p.headline ?? p.name}
            >
              <span className="rail-tkr mono">{p.ticker}</span>
              <span className="rail-px mono">{price(p.last_price)}</span>
              <span className={`rail-chg mono ${moveClass(p.change_percent)}`}>
                {move(p.change_percent)}
              </span>
            </NavLink>
          ))}
        </div>

        {/* The move is across the last 24 hours of the series, which is not
            the same as the change since yesterday's close. Saying which one
            it is costs a line and stops the number being read as something
            it is not. */}
        <div className="rail-foot">24h move · Loom's read in the left edge</div>
      </div>

      {reporting.length > 0 && (
        <div className="rail-block">
          <div className="rail-head"><span>Next to report</span></div>
          <div className="rail-rows">
            {reporting.map((e) => (
              <NavLink key={e.ticker} to={`/companies/${e.ticker}`} className="rail-row">
                <span className="rail-tkr mono">{e.ticker}</span>
                <span className="rail-when mono">
                  {e.days_until === 0
                    ? "today"
                    : e.days_until === 1
                      ? "tomorrow"
                      : `${e.days_until}d`}
                </span>
              </NavLink>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
