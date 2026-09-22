import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useBriefs } from "../hooks/useBriefs";
import { useDashboard } from "../hooks/useDashboard";
import { useExposure } from "../hooks/useExposure";
import { PropagationMap } from "../components/terminal/PropagationMap";
import { verdictOf, toneClass } from "../lib/plain";
import type { Brief, Stance } from "../types/models";

/** The analyst view: how companies are connected, and what an event reaches.
 *
 *  This started as a sortable table of every tracked company and was wrong in
 *  a way worth recording. A table is a screener: it hands a reader the raw
 *  material and leaves the synthesis to them, which is precisely the work this
 *  product exists to do. Anyone can sort a spreadsheet.
 *
 *  What the database holds that a spreadsheet cannot show is the relationships
 *  between companies, derived from what they disclose about each other. The
 *  question this view answers is not "rank these" but "if this one moves, what
 *  else does", which is the question that turns one filing into a position.
 */
export function TerminalPage() {
  const exposure = useExposure();
  const briefs = useBriefs(300);
  const dashboard = useDashboard();
  const [selected, setSelected] = useState<string | null>(null);

  const companyByTicker = useMemo(() => {
    const map = new Map<string, { id: string; name: string; sector: string | null }>();
    for (const row of dashboard.data?.companies ?? []) {
      map.set(row.ticker, { id: row.company_id, name: row.name, sector: row.sector });
    }
    return map;
  }, [dashboard.data]);

  const briefByTicker = useMemo(() => {
    const byId = new Map<string, Brief>((briefs.data ?? []).map((b) => [b.company_id, b]));
    const out = new Map<string, Brief>();
    for (const [ticker, company] of companyByTicker) {
      const brief = byId.get(company.id);
      if (brief) out.set(ticker, brief);
    }
    return out;
  }, [briefs.data, companyByTicker]);

  const stanceByTicker = useMemo(() => {
    const map = new Map<string, string>();
    for (const [ticker, brief] of briefByTicker) map.set(ticker, brief.stance);
    return map;
  }, [briefByTicker]);

  const graph = exposure.data;

  const detail = useMemo(() => {
    if (!graph || !selected) return null;
    const downstream = graph.edges
      .filter((e) => e.hub === selected)
      .sort((a, b) => b.mention_count - a.mention_count);
    const upstream = graph.edges
      .filter((e) => e.dependent === selected)
      .sort((a, b) => b.mention_count - a.mention_count);
    const node = graph.nodes.find((n) => n.ticker === selected);
    return { node, downstream, upstream };
  }, [graph, selected]);

  const hubs = useMemo(
    () => (graph?.nodes ?? []).filter((n) => n.reach >= 4).slice(0, 10),
    [graph],
  );

  if (exposure.isLoading) return <p className="empty-state">Building the graph…</p>;
  if (!graph || graph.nodes.length === 0) {
    return (
      <p className="empty-state">
        No dependency graph yet. It is built from what companies disclose about each
        other, so it needs filings ingested first.
      </p>
    );
  }

  return (
    <div>
      <header className="today-head">
        <h1>What moves what</h1>
        <p className="today-sub">
          Built from {graph.edges.length} dependencies Loom found by reading what these{" "}
          {graph.nodes.length} companies disclose about each other. Bigger circles are
          companies more others depend on. Click one to see what an event there reaches.
        </p>
      </header>

      <div className="grid grid-main-side">
        <div className="panel panel-body-flush">
          <PropagationMap
            graph={graph}
            stanceByTicker={stanceByTicker}
            selected={selected}
            onSelect={setSelected}
          />
        </div>

        <div className="stack">
          {detail && detail.node ? (
            <ExposureDetail
              ticker={selected as string}
              node={detail.node}
              downstream={detail.downstream}
              upstream={detail.upstream}
              brief={briefByTicker.get(selected as string)}
              companyName={companyByTicker.get(selected as string)?.name}
            />
          ) : (
            <div className="panel">
              <div className="panel-head"><span className="panel-title">Most connected</span></div>
              <table className="data-table">
                <thead>
                  <tr><th>Tkr</th><th className="num">Reach</th><th className="num">Upstream</th></tr>
                </thead>
                <tbody>
                  {hubs.map((n) => (
                    <tr key={n.ticker} className="clickable" onClick={() => setSelected(n.ticker)}>
                      <td><span className="ticker-symbol">{n.ticker}</span></td>
                      <td className="num">{n.reach}</td>
                      <td className="num dim">{n.upstream}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="faint" style={{ margin: 0, padding: "6px 8px", fontSize: 10, lineHeight: 1.5 }}>
                Reach is how many tracked companies move when this one does. Upstream is
                how many it is itself exposed to.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ExposureDetail({
  ticker, node, downstream, upstream, brief, companyName,
}: {
  ticker: string;
  node: { reach: number; upstream: number; sector: string | null };
  downstream: { dependent: string; mention_count: number }[];
  upstream: { hub: string; mention_count: number }[];
  brief?: Brief;
  companyName?: string;
}) {
  const verdict = brief ? verdictOf(brief.stance as Stance) : null;
  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">{ticker}</span>
        <Link className="faint" style={{ fontSize: 9 }} to={`/companies/${ticker}`}>OPEN →</Link>
      </div>
      <div style={{ padding: "8px 10px" }}>
        {companyName && <div className="faint" style={{ fontSize: 10, marginBottom: 5 }}>{companyName}</div>}
        <div className="faint" style={{ fontSize: 10, marginBottom: 5 }}>
          reaches {node.reach} · exposed to {node.upstream}
          {node.sector ? ` · ${node.sector}` : ""}
        </div>
        {verdict && (
          <div className={`${toneClass(verdict.tone)}`} style={{ fontSize: 13, fontWeight: 600 }}>
            {verdict.label}
          </div>
        )}
        {brief && (
          <p className="faint" style={{ margin: "4px 0 0", fontSize: 11, lineHeight: 1.5 }}>
            {brief.headline}
          </p>
        )}
      </div>

      {downstream.length > 0 && (
        <>
          <div className="panel-head" style={{ borderTop: "1px solid var(--border)" }}>
            <span className="panel-title">If {ticker} moves, these move</span>
            <span className="faint" style={{ fontSize: 9 }}>{downstream.length}</span>
          </div>
          <table className="data-table">
            <tbody>
              {downstream.map((e) => (
                <tr key={e.dependent}>
                  <td><Link className="ticker-symbol" to={`/companies/${e.dependent}`}>{e.dependent}</Link></td>
                  <td className="num dim">{e.mention_count} filings</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {upstream.length > 0 && (
        <>
          <div className="panel-head" style={{ borderTop: "1px solid var(--border)" }}>
            <span className="panel-title">{ticker} is exposed to</span>
            <span className="faint" style={{ fontSize: 9 }}>{upstream.length}</span>
          </div>
          <table className="data-table">
            <tbody>
              {upstream.map((e) => (
                <tr key={e.hub}>
                  <td><Link className="ticker-symbol" to={`/companies/${e.hub}`}>{e.hub}</Link></td>
                  <td className="num dim">{e.mention_count} filings</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <p className="faint" style={{ margin: 0, padding: "6px 8px", fontSize: 10, lineHeight: 1.5 }}>
        Measured by how often one company names another in its own filings. A count of
        mentions is a proxy for exposure, not a measurement of it, and it cannot tell a
        supplier from a landlord.
      </p>
    </div>
  );
}
