import { Fragment, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CompanyRowSignals } from "./CompanyRowSignals";
import { Sparkline } from "../shared/Sparkline";
import { relativeTime } from "../../lib/format";
import type { CompanyDashboardRow } from "../../types/models";

type SortKey =
  | "ticker" | "sentiment_score" | "sentiment_trend" | "bearish_count" | "bullish_count"
  | "major_count" | "pattern_count" | "risk_count" | "signal_count" | "avg_confidence"
  | "insider_net_usd" | "top_priority" | "last_signal_at";

const COLUMN_COUNT = 16;

function compactUsd(v: number): string {
  if (!v) return "-";
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "+";
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(0)}K`;
  return `${sign}${abs.toFixed(0)}`;
}

function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "value-neutral";
  return v > 0 ? "value-positive" : "value-negative";
}

interface Props {
  rows: CompanyDashboardRow[];
  isLoading: boolean;
  onRemove: (companyId: string) => void;
}

/** The comparison view: every company on one line, every column sortable.
 *
 *  Secondary to the briefs on purpose. This answers "how does X compare to Y
 *  on this measure", which is a question you can only ask once you already
 *  know what you are looking for. */
export function ScreenerTable({ rows, isLoading, onRemove }: Props) {
  const navigate = useNavigate();
  const [sortKey, setSortKey] = useState<SortKey>("top_priority");
  const [sortDesc, setSortDesc] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      const av = a[sortKey] ?? (sortKey === "ticker" ? "" : -Infinity);
      const bv = b[sortKey] ?? (sortKey === "ticker" ? "" : -Infinity);
      if (av < bv) return sortDesc ? 1 : -1;
      if (av > bv) return sortDesc ? -1 : 1;
      return 0;
    });
  }, [rows, sortKey, sortDesc]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortDesc((d) => !d);
    else { setSortKey(key); setSortDesc(true); }
  }

  const go = (ticker: string) => () => navigate(`/companies/${ticker}`);

  if (isLoading) return <div className="panel"><p className="empty-state">Loading…</p></div>;
  if (sorted.length === 0) {
    return <div className="panel"><div className="empty-state">No companies yet.</div></div>;
  }

  return (
    <div className="panel">
      <table className="data-table">
        <thead>
          <tr>
            <Th label="Ticker" k="ticker" {...{ sortKey, sortDesc, toggleSort }} />
            <th>Name</th>
            <Th label="Tone" num k="sentiment_score" {...{ sortKey, sortDesc, toggleSort }} />
            <Th label="Δ" num k="sentiment_trend" {...{ sortKey, sortDesc, toggleSort }} />
            <th className="center">Trend</th>
            <Th label="Neg" num k="bearish_count" {...{ sortKey, sortDesc, toggleSort }} />
            <Th label="Pos" num k="bullish_count" {...{ sortKey, sortDesc, toggleSort }} />
            <Th label="Major" num k="major_count" {...{ sortKey, sortDesc, toggleSort }} />
            <Th label="Patt" num k="pattern_count" {...{ sortKey, sortDesc, toggleSort }} />
            <Th label="Risks" num k="risk_count" {...{ sortKey, sortDesc, toggleSort }} />
            <Th label="Findings" num k="signal_count" {...{ sortKey, sortDesc, toggleSort }} />
            <Th label="Conf" num k="avg_confidence" {...{ sortKey, sortDesc, toggleSort }} />
            <Th label="Insider net" num k="insider_net_usd" {...{ sortKey, sortDesc, toggleSort }} />
            <th>Top finding</th>
            <Th label="Upd" num k="last_signal_at" {...{ sortKey, sortDesc, toggleSort }} />
            <th style={{ width: 20 }}></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <Fragment key={row.company_id}>
              <tr className="clickable">
                <td onClick={() => setExpanded(expanded === row.ticker ? null : row.ticker)}>
                  <span className="faint" style={{ marginRight: 3 }}>
                    {expanded === row.ticker ? "▾" : "▸"}
                  </span>
                  <span className="ticker-symbol">{row.ticker}</span>
                </td>
                <td className="dim" onClick={go(row.ticker)} style={{ maxWidth: 150 }}>{row.name}</td>
                <td className={`num ${signClass(row.sentiment_score)}`} onClick={go(row.ticker)}>
                  {row.sentiment_score === null ? "-" : row.sentiment_score.toFixed(2)}
                </td>
                <td
                  className={`num ${row.sentiment_trend ? (row.sentiment_trend > 0 ? "cell-pos" : "cell-neg") : "value-neutral"}`}
                  onClick={go(row.ticker)}
                >
                  {row.sentiment_trend === null ? "-" : `${row.sentiment_trend > 0 ? "+" : ""}${row.sentiment_trend.toFixed(2)}`}
                </td>
                <td className="center" onClick={go(row.ticker)}><Sparkline points={row.sentiment_history} /></td>
                <td className={`num ${row.bearish_count ? "value-negative" : "faint"}`} onClick={go(row.ticker)}>{row.bearish_count || "-"}</td>
                <td className={`num ${row.bullish_count ? "value-positive" : "faint"}`} onClick={go(row.ticker)}>{row.bullish_count || "-"}</td>
                <td className={`num ${row.major_count ? "value-negative" : "faint"}`} onClick={go(row.ticker)}>{row.major_count || "-"}</td>
                <td className="num" onClick={go(row.ticker)}>
                  {row.pattern_count ? <span className="tag tag-accent">{row.pattern_count}</span> : <span className="faint">-</span>}
                </td>
                <td className={`num ${row.risk_count ? "value-negative" : "faint"}`} onClick={go(row.ticker)}>{row.risk_count || "-"}</td>
                <td className="num dim" onClick={go(row.ticker)}>{row.signal_count || "-"}</td>
                <td className="num dim" onClick={go(row.ticker)}>
                  {row.avg_confidence === null ? "-" : Math.round(row.avg_confidence * 100)}
                </td>
                <td
                  className={`num ${signClass(row.insider_net_usd)}`}
                  onClick={go(row.ticker)}
                  title="Open-market insider buys minus sells, last 90 days"
                >
                  {compactUsd(row.insider_net_usd)}
                </td>
                <td className="prose" onClick={go(row.ticker)}>
                  {row.top_signal ? row.top_signal.summary : <span className="faint">no findings yet</span>}
                </td>
                <td className="num faint" onClick={go(row.ticker)}>{relativeTime(row.last_signal_at)}</td>
                <td onClick={(e) => e.stopPropagation()} className="center">
                  <button className="link-button faint" title="Remove from watchlist" onClick={() => onRemove(row.company_id)}>
                    ×
                  </button>
                </td>
              </tr>
              {expanded === row.ticker && <CompanyRowSignals ticker={row.ticker} colSpan={COLUMN_COUNT} />}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({
  label, k, num, sortKey, sortDesc, toggleSort,
}: {
  label: string; k: SortKey; num?: boolean;
  sortKey: SortKey; sortDesc: boolean; toggleSort: (k: SortKey) => void;
}) {
  const active = sortKey === k;
  return (
    <th className={`sortable ${num ? "num" : ""}`} onClick={() => toggleSort(k)}>
      {label}
      {active && <span style={{ color: "var(--accent)" }}>{sortDesc ? "▾" : "▴"}</span>}
    </th>
  );
}
