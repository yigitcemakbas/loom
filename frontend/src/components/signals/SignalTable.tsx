import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { useAnnotateSignal, useDismissSignal } from "../../hooks/useSignals";
import type { MarketDirection, Signal, SignalType } from "../../types/models";

const TYPE_LABELS: Record<SignalType, string> = {
  sentiment_shift: "SENT",
  new_risk_factor: "RISK",
  notable_quote: "QUOTE",
  qoq_anomaly: "YOY",
  guidance_change: "GUID",
  emerging_pattern: "PTRN",
  insider_activity: "INSDR",
  short_interest_spike: "SHORT",
};

const HORIZON_LABELS: Record<string, string> = {
  near_term: "NEAR",
  multi_quarter: "MULTI-Q",
  structural: "STRUCT",
};

const MAGNITUDE_LABELS: Record<string, string> = {
  minor: "MIN",
  moderate: "MOD",
  major: "MAJ",
};

function typeClass(t: SignalType): string {
  if (t === "emerging_pattern") return "tag tag-accent";
  if (t === "new_risk_factor" || t === "qoq_anomaly") return "tag tag-risk";
  return "tag";
}

function dirClass(d: MarketDirection | null): string {
  if (d === "positive") return "value-positive";
  if (d === "negative") return "value-negative";
  return "faint";
}

function dirGlyph(d: MarketDirection | null): string {
  if (d === "positive") return "▲";
  if (d === "negative") return "▼";
  if (d === "neutral") return "■";
  return "-";
}

interface Props {
  signals: Signal[];
  showTicker?: boolean;
  emptyMessage?: string;
}

/** Signals as a data grid, not a stack of paragraphs.
 *
 *  Every attribute a signal carries, direction, magnitude, horizon,
 *  confidence, priority, is a sortable column rather than a phrase buried in a
 *  sentence, which is what makes fifty findings scannable instead of a wall of
 *  text. The prose is truncated to exactly one line and never sets row height;
 *  clicking a row opens the full narrative, the verbatim evidence, and the
 *  actions beneath it. */
export function SignalTable({ signals, showTicker = true, emptyMessage }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [noteFor, setNoteFor] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const annotate = useAnnotateSignal();
  const dismiss = useDismissSignal();

  if (signals.length === 0) {
    return <div className="empty-state">{emptyMessage ?? "No signals match this filter."}</div>;
  }

  const cols = showTicker ? 10 : 9;

  function startNote(signal: Signal) {
    setNoteFor(signal.id);
    setDraft(signal.note ?? "");
  }

  function saveNote(id: string) {
    if (!draft.trim()) return;
    annotate.mutate({ id, note: draft.trim() }, { onSuccess: () => setNoteFor(null) });
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th className="num" style={{ width: 62 }}>Date</th>
          {showTicker && <th style={{ width: 52 }}>Tkr</th>}
          <th style={{ width: 52 }}>Type</th>
          <th className="center" style={{ width: 30 }}>Dir</th>
          <th className="center" style={{ width: 38 }}>Mag</th>
          <th style={{ width: 62 }}>Horizon</th>
          <th className="num" style={{ width: 40 }}>Conf</th>
          <th className="num" style={{ width: 40 }}>Pri</th>
          <th>Finding</th>
          <th style={{ width: 34 }}></th>
        </tr>
      </thead>
      <tbody>
        {signals.map((s) => {
          const isOpen = open === s.id;
          const dismissed = Boolean(s.dismissed_at);
          return (
            <Fragment key={s.id}>
              <tr
                className="clickable"
                style={{ opacity: dismissed ? 0.4 : 1 }}
                onClick={() => setOpen(isOpen ? null : s.id)}
              >
                <td className="num faint">{new Date(s.occurred_at).toLocaleDateString(undefined, { year: "2-digit", month: "2-digit", day: "2-digit" })}</td>
                {showTicker && (
                  <td onClick={(e) => e.stopPropagation()}>
                    <Link className="ticker-symbol" to={`/companies/${s.ticker}`}>{s.ticker}</Link>
                  </td>
                )}
                <td><span className={typeClass(s.signal_type)}>{TYPE_LABELS[s.signal_type]}</span></td>
                <td className={`center ${dirClass(s.market_direction)}`}>{dirGlyph(s.market_direction)}</td>
                <td className={`center ${s.market_magnitude === "major" ? "value-negative" : "dim"}`}>
                  {s.market_magnitude ? MAGNITUDE_LABELS[s.market_magnitude] : "-"}
                </td>
                <td className="faint">{s.market_horizon ? HORIZON_LABELS[s.market_horizon] : "-"}</td>
                <td className="num dim">{Math.round(s.confidence * 100)}</td>
                <td className="num dim">{s.priority.toFixed(2)}</td>
                <td className="prose">{s.summary}</td>
                <td className="center faint">
                  {!s.reviewed_at && !dismissed && <span className="tag tag-accent">N</span>}
                  {s.note && <span title="annotated"> ✎</span>}
                </td>
              </tr>

              {isOpen && (
                <tr className="detail-row">
                  <td colSpan={cols}>
                    <p className="detail-prose">{s.summary}</p>
                    {s.detail && (
                      <p className="detail-prose dim" style={{ fontSize: 11 }}>
                        <span className={dirClass(s.market_direction)} style={{ textTransform: "uppercase" }}>
                          {s.market_direction ?? ""}
                        </span>{" "}
                        {s.detail}
                      </p>
                    )}
                    {s.pattern_document_count && (
                      <p className="faint" style={{ margin: "0 0 5px", fontSize: 10 }}>
                        synthesised from {s.pattern_document_count} disclosures
                        {s.pattern_window_days ? ` across ${s.pattern_window_days} days` : ""}
                      </p>
                    )}
                    {s.evidence_quote && <blockquote className="evidence">{s.evidence_quote}</blockquote>}

                    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 6, alignItems: "center" }}>
                      {s.source_url && (
                        <a className="mono" style={{ fontSize: 10 }} href={s.source_url} target="_blank" rel="noreferrer">
                          SOURCE →
                        </a>
                      )}
                      {s.compared_source_url && (
                        <a className="mono" style={{ fontSize: 10 }} href={s.compared_source_url} target="_blank" rel="noreferrer">
                          COMPARED AGAINST →
                        </a>
                      )}
                      {noteFor === s.id ? (
                        <span style={{ display: "flex", gap: 5, flex: 1, minWidth: 260 }}>
                          <input
                            type="text"
                            value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            placeholder="your take on this finding…"
                            style={{ flex: 1 }}
                            autoFocus
                          />
                          <button className="btn" onClick={() => saveNote(s.id)} disabled={annotate.isPending || !draft.trim()}>save</button>
                          <button className="link-button" onClick={() => setNoteFor(null)}>cancel</button>
                        </span>
                      ) : (
                        <>
                          <button className="link-button" onClick={() => startNote(s)}>
                            {s.note ? "edit note" : "add note"}
                          </button>
                          {!dismissed && (
                            <button className="link-button" onClick={() => dismiss.mutate(s.id)} disabled={dismiss.isPending}>
                              dismiss
                            </button>
                          )}
                        </>
                      )}
                    </div>

                    {s.note && noteFor !== s.id && (
                      <div className="evidence" style={{ borderLeftColor: "var(--accent)", marginTop: 6 }}>
                        <span className="mono" style={{ color: "var(--accent)", fontSize: 9 }}>YOUR NOTE</span>
                        <div>{s.note}</div>
                      </div>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
