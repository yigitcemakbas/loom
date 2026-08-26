import { Link } from "react-router-dom";
import type { Brief, Stance } from "../../types/models";

/** Colour and weight per verdict. Only the stance carries colour on this card,
 *  so the eye lands on the answer before anything else. */
const STANCE_STYLE: Record<Stance, { color: string; border: string }> = {
  strong_negative: { color: "var(--negative)", border: "var(--negative)" },
  negative: { color: "var(--negative)", border: "var(--negative-dim)" },
  mixed: { color: "var(--warn)", border: "var(--accent-dim)" },
  positive: { color: "var(--positive)", border: "var(--positive-dim)" },
  strong_positive: { color: "var(--positive)", border: "var(--positive)" },
  quiet: { color: "var(--text-dim)", border: "var(--border-strong)" },
  insufficient: { color: "var(--text-faint)", border: "var(--border)" },
};

function directionMark(direction: string): { glyph: string; cls: string } {
  if (direction === "negative") return { glyph: "▼", cls: "value-negative" };
  if (direction === "positive") return { glyph: "▲", cls: "value-positive" };
  if (direction === "unassessed") return { glyph: "?", cls: "faint" };
  return { glyph: "■", cls: "value-neutral" };
}

const SOURCE_LABELS: Record<string, string> = {
  "10-K": "annual report",
  "10-Q": "quarterly report",
  "8-K": "company announcement",
  earnings_call: "earnings call",
  news: "news coverage",
  insider: "insider trades",
  filing: "regulatory filing",
};

interface Props {
  brief: Brief;
  ticker: string;
  name: string;
  compact?: boolean;
  /** Off on the company page, where the quote header already names the
   *  company one line above and repeating it just costs vertical space. */
  showIdentity?: boolean;
}

/** One company's answer, not its evidence.
 *
 *  The reading order is deliberate and is the whole point of the component:
 *  verdict, then the one sentence explaining it, then the two or three things
 *  driving it, then what is new since last time. A reader who stops after the
 *  first line should still have learned the single most useful thing Loom
 *  knows about this company. Everything below it is support for that line,
 *  not a substitute for it. */
export function BriefCard({ brief, ticker, name, compact = false, showIdentity = true }: Props) {
  const style = STANCE_STYLE[brief.stance];
  const hasView = brief.stance !== "insufficient";

  return (
    <div className="panel" style={{ borderLeft: `3px solid ${style.border}` }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "7px 10px 4px", flexWrap: "wrap" }}>
        {showIdentity && (
          <>
            <Link className="ticker-symbol" style={{ fontSize: 15 }} to={`/companies/${ticker}`}>
              {ticker}
            </Link>
            <span className="dim" style={{ fontSize: 11 }}>{name}</span>
          </>
        )}

        <span
          style={{
            marginLeft: "auto",
            color: style.color,
            fontSize: 12,
            letterSpacing: "0.07em",
            textTransform: "uppercase",
            whiteSpace: "nowrap",
          }}
        >
          {brief.stance_label}
        </span>
        {hasView && (
          <span className="faint" style={{ fontSize: 10, whiteSpace: "nowrap" }}>
            {Math.round(brief.confidence * 100)}% confidence
          </span>
        )}
      </div>

      <p
        className="sans"
        style={{ margin: 0, padding: "0 10px 8px", fontSize: 13, lineHeight: 1.5, color: "var(--text)" }}
      >
        {brief.headline}
      </p>

      {!compact && brief.drivers.length > 0 && (
        <>
          <div className="panel-head" style={{ borderTop: "1px solid var(--border)" }}>
            <span className="panel-title">What's driving this</span>
            <span className="faint" style={{ fontSize: 9 }}>FROM {brief.signal_count} FINDINGS</span>
          </div>
          <div style={{ padding: "4px 10px 6px" }}>
            {brief.drivers.map((d, i) => {
              const mark = directionMark(d.direction);
              return (
                <div
                  key={i}
                  style={{
                    padding: "4px 0",
                    borderBottom: i < brief.drivers.length - 1 ? "1px solid var(--border)" : "none",
                  }}
                >
                  <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                    <span className={mark.cls} style={{ fontSize: 10 }}>{mark.glyph}</span>
                    <span className="sans" style={{ fontSize: 12, color: "var(--text)" }}>{d.title}</span>
                    <span className="faint" style={{ fontSize: 9, marginLeft: "auto", whiteSpace: "nowrap" }}>
                      {d.sources.map((s) => SOURCE_LABELS[s] ?? s).join(" · ")}
                    </span>
                  </div>
                  {d.detail && (
                    <p className="sans dim" style={{ margin: "2px 0 0 16px", fontSize: 11, lineHeight: 1.45 }}>
                      {d.detail}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {brief.what_changed && (
        <div style={{ padding: "5px 10px", borderTop: "1px solid var(--border)", background: "var(--bg-inset)" }}>
          <span className="mono" style={{ fontSize: 9, color: "var(--accent)", letterSpacing: "0.08em" }}>
            NEW SINCE LAST READ
          </span>
          <p className="sans dim" style={{ margin: "2px 0 0", fontSize: 11, lineHeight: 1.45 }}>
            {brief.what_changed}
          </p>
        </div>
      )}
    </div>
  );
}
