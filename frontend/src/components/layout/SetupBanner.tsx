import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../../api/client";

interface Capability {
  active: boolean;
  needs_key?: boolean;
  key_name?: string;
  get_key_at?: string;
}

interface Capabilities {
  sources: Record<string, Capability>;
  analysis: Capability & { provider: string };
}

const SOURCE_LABELS: Record<string, string> = {
  company_news: "company news",
  earnings_calendar: "earnings dates",
};

/** Says plainly what is switched off, and how to switch it on.
 *
 *  Loom runs with no configuration at all: filings, transcripts, insider
 *  records, prices and search all work unconfigured. Two things need a free
 *  key. Rather than let someone discover that by clicking a button that
 *  quietly does nothing, this states it once, at the top, with the link. */
export function SetupBanner() {
  const { data } = useQuery<Capabilities>({
    queryKey: ["capabilities"],
    queryFn: async () => (await apiClient.get<Capabilities>("/capabilities")).data,
    staleTime: 5 * 60_000,
  });

  if (!data) return null;

  const missingSources = Object.entries(data.sources)
    .filter(([, c]) => c.needs_key && !c.active)
    .map(([k]) => SOURCE_LABELS[k] ?? k.replace(/_/g, " "));

  const needsAnalysis = !data.analysis.active;
  if (!needsAnalysis && missingSources.length === 0) return null;

  return (
    <div
      className="panel"
      style={{ borderLeft: "3px solid var(--accent-dim)", padding: "6px 10px", marginBottom: 8 }}
    >
      <span className="mono" style={{ fontSize: 9, color: "var(--accent)", letterSpacing: "0.08em" }}>
        OPTIONAL SETUP
      </span>
      <p className="sans dim" style={{ margin: "3px 0 0", fontSize: 11, lineHeight: 1.5 }}>
        Loom is running. Filings, transcripts, insider records, prices and search all work as they are.
        {needsAnalysis && (
          <>
            {" "}To have it <strong style={{ color: "var(--text)" }}>read and summarise</strong> what it
            collects, add a free{" "}
            <a href={data.analysis.get_key_at} target="_blank" rel="noreferrer">
              {data.analysis.provider} key
            </a>{" "}
            as <code>{data.analysis.key_name}</code>.
          </>
        )}
        {missingSources.length > 0 && (
          <>
            {" "}For {missingSources.join(" and ")}, add a free{" "}
            <a href="https://finnhub.io/register" target="_blank" rel="noreferrer">Finnhub key</a>{" "}
            as <code>FINNHUB_API_KEY</code>.
          </>
        )}
        {" "}Put either in a <code>.env</code> file beside <code>docker-compose.yml</code>, then run{" "}
        <code>docker compose up -d</code> again.
      </p>
    </div>
  );
}
