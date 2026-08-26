import { useSystemStatus } from "../hooks/useAnalysisStatus";

/** document_analyses and llm_usage_runs are populated on every analysis
 * run and, until this page, were never displayed anywhere, analysis
 * failures were invisible and spend vanished the moment a script exited. */
export function SystemStatusPage() {
  const { data, isLoading, isError } = useSystemStatus();

  if (isError) {
    return <p className="error-text">Can't reach the Loom API. Confirm the backend is running and reload.</p>;
  }
  if (isLoading || !data) {
    return <p className="empty-state">Loading…</p>;
  }

  return (
    <div>
      <div className="page-title-row">
        <h2>System</h2>
      </div>

      <div className="stat-strip" style={{ marginBottom: 8 }}>
        <Stat label="Analysis runs" value={String(data.total_runs)} />
        <Stat label="Failed" value={String(data.failed_runs)} valueClass={data.failed_runs > 0 ? "value-negative" : "value-positive"} />
        <Stat label="LLM calls" value={String(data.total_calls)} />
        <Stat label="Total cost" value={data.total_cost_usd > 0 ? `$${data.total_cost_usd.toFixed(2)}` : "free tier"} />
      </div>

      <div className="panel" style={{ marginBottom: 8 }}>
        <div className="panel-head"><span className="panel-title">Usage by run</span></div>
        {data.usage_runs.length === 0 ? (
          <p className="empty-state">No usage recorded yet, analysis batches record here once at least one LLM call succeeds.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 60 }}>Ticker</th>
                <th style={{ width: 80 }}>Provider</th>
                <th style={{ width: 150 }}>Model</th>
                <th className="num" style={{ width: 55 }}>Calls</th>
                <th className="num" style={{ width: 80 }}>In tok</th>
                <th className="num" style={{ width: 80 }}>Out tok</th>
                <th className="num" style={{ width: 60 }}>Cost</th>
                <th className="num" style={{ width: 130 }}>When</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.usage_runs.map((u) => (
                <tr key={u.id}>
                  <td className="ticker-symbol">{u.ticker}</td>
                  <td>{u.provider}</td>
                  <td className="mono">{u.model}</td>
                  <td className="num mono">{u.calls}</td>
                  <td className="num mono">{u.input_tokens.toLocaleString()}</td>
                  <td className="num mono">{u.output_tokens.toLocaleString()}</td>
                  <td className="num mono">{u.cost_usd > 0 ? `$${u.cost_usd.toFixed(4)}` : "free"}</td>
                  <td className="num faint">{new Date(u.created_at).toLocaleString()}</td>
                  <td></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <div className="panel-head"><span className="panel-title">Analysis job history</span></div>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 60 }}>Ticker</th>
              <th style={{ width: 90 }}>Type</th>
              <th style={{ width: 80 }}>Status</th>
              <th className="num" style={{ width: 60 }}>Signals</th>
              <th className="num" style={{ width: 130 }}>When</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {data.analysis_runs.map((r) => (
              <tr key={r.id}>
                <td className="ticker-symbol">{r.ticker}</td>
                <td>{r.doc_subtype && <span className="tag">{r.doc_subtype}</span>}</td>
                <td>
                  <span className={r.status === "failed" ? "tag tag-risk" : "tag tag-positive"}>{r.status}</span>
                </td>
                <td className="num dim">{r.signal_count}</td>
                <td className="num faint">{new Date(r.created_at).toLocaleString()}</td>
                <td className="prose value-negative">{r.error ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">
        {label}
      </div>
      <div className={`stat-value ${valueClass ?? ""}`}>{value}</div>
    </div>
  );
}
