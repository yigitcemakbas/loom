import { useEffect, useState } from "react";
import { LoomLogo } from "../components/layout/LoomMark";

/** The left half of the sign-in screen: the size of what this instance holds.
 *
 *  Two earlier versions were wrong in opposite directions. The first was a
 *  headline, a paragraph and three bulleted claims — landing-page copy on a
 *  screen where the reader has already decided to use the product. The second
 *  replaced it with a source list reading "active" six times and a line saying
 *  "Analysis — Online", which is the same emptiness in a terminal font: a
 *  connector being enabled is a fact about configuration, not about Loom, and
 *  "online" restates that the page you are looking at loaded.
 *
 *  This reports volume instead. Counts are the one thing a research tool can
 *  state on a sign-in screen that a reader will actually weigh, because they
 *  bound what it could possibly know. They are read live, so they cannot drift
 *  into being a claim, and `latest_document` is there to stop volume reading as
 *  currency: a large corpus that stopped updating is a different proposition
 *  from a large one that is current. */

interface Stats {
  companies_tracked: number;
  companies_read: number;
  documents: number;
  sec_filings: number;
  transcripts: number;
  news_items: number;
  findings: number;
  latest_document: string | null;
}

type State = "loading" | "ready" | "unreachable";

const n = (v: number) => v.toLocaleString("en-US");

function freshness(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export function AuthSystemPanel() {
  const [state, setState] = useState<State>("loading");
  const [s, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/coverage-stats")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data) => !cancelled && (setStats(data), setState("ready")))
      .catch(() => !cancelled && setState("unreachable"));
    return () => {
      cancelled = true;
    };
  }, []);

  // Em dash while loading rather than a skeleton or a spinner: the figures
  // arrive in one request and a placeholder that moves would be the loudest
  // thing on a sign-in screen.
  const v = (k: keyof Stats) => (s ? n(s[k] as number) : "—");

  return (
    <section className="auth-pitch">
      <div className="auth-brand">
        <LoomLogo size={28} subtitle="DWYCA" />
      </div>

      <p className="auth-role">Equity research terminal</p>

      {state === "unreachable" ? (
        <p className="auth-source-down">
          This instance is not responding. The interface is served separately, so
          the page loads, but sign-in will fail until the backend is running.
        </p>
      ) : (
        <>
          {/* The headline pair. Tracked is the universe; read is how much of it
              Loom has actually opened — printing only the larger number would
              be the flattering half of the truth. */}
          <div className="auth-figures">
            <div>
              <span className="auth-figure mono">{v("companies_tracked")}</span>
              <span className="auth-figure-label">Companies tracked</span>
            </div>
            <div>
              <span className="auth-figure mono">{v("documents")}</span>
              <span className="auth-figure-label">Documents held</span>
            </div>
          </div>

          <div className="auth-readout">
            <div className="auth-readout-head">Corpus</div>
            <dl className="auth-stats">
              <div>
                <dt>SEC filings</dt>
                <dd className="mono">{v("sec_filings")}</dd>
              </div>
              <div>
                <dt>News items</dt>
                <dd className="mono">{v("news_items")}</dd>
              </div>
              <div>
                <dt>Earnings transcripts</dt>
                <dd className="mono">{v("transcripts")}</dd>
              </div>
              <div>
                <dt>Companies read in depth</dt>
                <dd className="mono">{v("companies_read")}</dd>
              </div>
            </dl>
          </div>

          <div className="auth-readout">
            <div className="auth-readout-head">Extraction</div>
            <dl className="auth-stats">
              <div>
                <dt>Findings with a source passage</dt>
                <dd className="mono">{v("findings")}</dd>
              </div>
              <div>
                <dt>Most recent document</dt>
                <dd className="mono">{s ? freshness(s.latest_document) : "—"}</dd>
              </div>
            </dl>
          </div>
        </>
      )}

      <p className="auth-foot">
        Point-in-time reconstruction · every finding resolves to its source document
      </p>
    </section>
  );
}
