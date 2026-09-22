import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useDeletePosition, usePortfolio, useSavePosition } from "../hooks/usePortfolio";
import { DigestSetting } from "../components/portfolio/DigestSetting";
import { useDashboard } from "../hooks/useDashboard";
import { verdictOf, toneClass } from "../lib/plain";
import type { Position, Stance } from "../types/models";

/** What you own, and what Loom makes of it.
 *
 *  The page that changes what Loom is. Every other view describes companies;
 *  this one is the only place where Loom knows which companies are *yours*,
 *  which is what lets it say "the case for something you hold has weakened"
 *  rather than publishing a hundred and thirty equally-weighted opinions and
 *  leaving the reader to find the one that concerns them.
 *
 *  Holdings and watch items live in one table because the difference is a
 *  single number. Two tables would mean two lists to keep in step and a
 *  decision to make every time you add a company. */
export function PortfolioPage() {
  const { data, isLoading } = usePortfolio();
  const save = useSavePosition();
  const remove = useDeletePosition();
  const [adding, setAdding] = useState(false);

  const held = useMemo(() => (data?.positions ?? []).filter((p) => p.is_held), [data]);
  const watching = useMemo(() => (data?.positions ?? []).filter((p) => !p.is_held), [data]);

  if (isLoading) return <p className="empty-state">Loading your portfolio…</p>;

  const empty = (data?.positions.length ?? 0) === 0;

  return (
    <div>
      <header className="today-head">
        <h1>Your portfolio</h1>
        <p className="today-sub">
          Tell Loom what you own and it stops treating all {data ? "" : ""}companies
          as equally interesting. Positions drive what gets surfaced first on Today
          and in What changed, and they are the only way Loom can tell you that the
          case for something you hold has weakened.
        </p>
      </header>

      {data && (data.total_value !== null || data.held_count > 0) && (
        <div className="portfolio-summary">
          <Figure label="Positions" value={`${data.held_count}`} sub={`${data.watching_count} watched`} />
          <Figure label="Market value" value={money(data.total_value)} />
          <Figure label="Cost" value={money(data.total_cost)} />
          <Figure
            label="Unrealised"
            value={money(data.total_unrealised)}
            tone={data.total_unrealised === null ? undefined
              : data.total_unrealised >= 0 ? "value-positive" : "value-negative"}
          />
        </div>
      )}

      {empty && (
        <p className="empty-state" style={{ padding: "18px 0" }}>
          Nothing here yet. Add a company you own, or one you are considering, and
          Loom will start putting it first.
        </p>
      )}

      {held.length > 0 && (
        <PositionTable
          title="Held"
          rows={held}
          onRemove={(t) => remove.mutate(t)}
          onSave={(t, input) => save.mutate({ ticker: t, input })}
        />
      )}
      {watching.length > 0 && (
        <PositionTable
          title="Watching"
          rows={watching}
          onRemove={(t) => remove.mutate(t)}
          onSave={(t, input) => save.mutate({ ticker: t, input })}
        />
      )}

      {/* Beside what it reports on, not in a settings screen two clicks away. */}
      <DigestSetting />

      {adding ? (
        <AddPosition
          onCancel={() => setAdding(false)}
          onSave={(ticker, input) => {
            save.mutate({ ticker, input });
            setAdding(false);
          }}
        />
      ) : (
        <button className="btn" onClick={() => setAdding(true)} style={{ marginTop: 10 }}>
          + add a position
        </button>
      )}
    </div>
  );
}

function Figure({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="portfolio-figure">
      <div className="pf-label">{label}</div>
      <div className={`pf-value ${tone ?? ""}`}>{value}</div>
      {sub && <div className="pf-sub">{sub}</div>}
    </div>
  );
}

function PositionTable({
  title, rows, onRemove, onSave,
}: {
  title: string;
  rows: Position[];
  onRemove: (ticker: string) => void;
  onSave: (ticker: string, input: { shares?: number | null; cost_basis?: number | null }) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-head">
        <span className="panel-title">{title}</span>
        <span className="faint" style={{ fontSize: 9 }}>{rows.length}</span>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Tkr</th>
            <th className="num">Shares</th>
            <th className="num">Cost</th>
            <th className="num">Price</th>
            <th className="num">Value</th>
            <th className="num">P/L</th>
            <th>Loom says</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.ticker}>
              <td><Link className="ticker-symbol" to={`/companies/${p.ticker}`}>{p.ticker}</Link></td>
              <td className="num">{p.shares !== null ? fmt(p.shares) : "-"}</td>
              <td className="num dim">{p.cost_basis !== null ? p.cost_basis.toFixed(2) : "-"}</td>
              <td className="num">{p.last_price !== null ? p.last_price.toFixed(2) : "-"}</td>
              <td className="num">{p.market_value !== null ? fmt(p.market_value, 0) : "-"}</td>
              <td className={`num ${p.unrealised === null ? "" : p.unrealised >= 0 ? "value-positive" : "value-negative"}`}>
                {p.unrealised === null ? "-" : `${p.unrealised >= 0 ? "+" : ""}${fmt(p.unrealised, 0)}`}
                {p.unrealised_percent !== null && (
                  <span className="faint" style={{ fontSize: 9 }}> {p.unrealised_percent >= 0 ? "+" : ""}{p.unrealised_percent}%</span>
                )}
              </td>
              <td className="prose-wrap" style={{ fontSize: 10.5 }}>
                {p.stance ? (
                  <span className={toneClass(verdictOf(p.stance as Stance).tone)}>
                    {verdictOf(p.stance as Stance).label}
                  </span>
                ) : <span className="faint">no view yet</span>}
              </td>
              <td className="num">
                <button className="row-action" onClick={() => setEditing(editing === p.ticker ? null : p.ticker)}>edit</button>
                <button className="row-action danger" onClick={() => onRemove(p.ticker)}>×</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <EditRow
          ticker={editing}
          existing={rows.find((r) => r.ticker === editing)}
          onCancel={() => setEditing(null)}
          onSave={(input) => { onSave(editing, input); setEditing(null); }}
        />
      )}
    </div>
  );
}

function EditRow({
  ticker, existing, onCancel, onSave,
}: {
  ticker: string;
  existing?: Position;
  onCancel: () => void;
  onSave: (input: { shares?: number | null; cost_basis?: number | null }) => void;
}) {
  const [shares, setShares] = useState(existing?.shares?.toString() ?? "");
  const [cost, setCost] = useState(existing?.cost_basis?.toString() ?? "");

  return (
    <div className="position-edit">
      <span className="ticker-symbol">{ticker}</span>
      <input className="pos-input" placeholder="shares" value={shares} inputMode="decimal"
        onChange={(e) => setShares(e.target.value)} />
      <input className="pos-input" placeholder="cost per share" value={cost} inputMode="decimal"
        onChange={(e) => setCost(e.target.value)} />
      <button className="btn" onClick={() => onSave({
        // Empty means "not a holding any more", which is how a position
        // becomes a watch item without deleting the row and losing the note.
        shares: shares.trim() === "" ? null : Number(shares),
        cost_basis: cost.trim() === "" ? null : Number(cost),
      })}>save</button>
      <button className="row-action" onClick={onCancel}>cancel</button>
    </div>
  );
}

function AddPosition({
  onCancel, onSave,
}: {
  onCancel: () => void;
  onSave: (ticker: string, input: { shares?: number | null; cost_basis?: number | null }) => void;
}) {
  const { data } = useDashboard();
  const [ticker, setTicker] = useState("");
  const [shares, setShares] = useState("");
  const [cost, setCost] = useState("");

  const known = useMemo(
    () => new Set((data?.companies ?? []).map((c) => c.ticker)),
    [data],
  );
  const symbol = ticker.trim().toUpperCase();
  const valid = known.has(symbol);

  return (
    <div className="position-edit">
      <input className="pos-input" placeholder="ticker" value={ticker} autoFocus
        onChange={(e) => setTicker(e.target.value.toUpperCase())} list="known-tickers" />
      <datalist id="known-tickers">
        {(data?.companies ?? []).map((c) => <option key={c.ticker} value={c.ticker}>{c.name}</option>)}
      </datalist>
      <input className="pos-input" placeholder="shares (optional)" value={shares} inputMode="decimal"
        onChange={(e) => setShares(e.target.value)} />
      <input className="pos-input" placeholder="cost per share" value={cost} inputMode="decimal"
        onChange={(e) => setCost(e.target.value)} />
      <button className="btn" disabled={!valid} onClick={() => onSave(symbol, {
        shares: shares.trim() === "" ? null : Number(shares),
        cost_basis: cost.trim() === "" ? null : Number(cost),
      })}>add</button>
      <button className="row-action" onClick={onCancel}>cancel</button>
      {/* Said rather than silently disabled: a person typing a ticker Loom
          does not follow deserves to know that is why nothing happens. */}
      {ticker.trim() !== "" && !valid && (
        <span className="faint" style={{ fontSize: 10 }}>Loom does not track {symbol} yet.</span>
      )}
    </div>
  );
}

function fmt(n: number, places = 2): string {
  return n.toLocaleString(undefined, { minimumFractionDigits: places, maximumFractionDigits: places });
}

function money(n: number | null): string {
  if (n === null) return "-";
  return `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
