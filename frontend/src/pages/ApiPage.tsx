import { useState } from "react";
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from "../hooks/useApiKeys";
import type { ApiKeyCreated } from "../api/keys";

/** Programmatic access: keys, and what they open.
 *
 *  The key system existed in full on the server — issue, list, revoke,
 *  read-only scoping — with no way to reach it from the interface, so the only
 *  route to a key was curl. That is the wrong front door for the readers this
 *  API is built for.
 *
 *  The endpoint reference lives on the same page as the key rather than in a
 *  README. Someone who has just issued a credential is, at that exact moment,
 *  the person who needs to know what it opens; sending them elsewhere to find
 *  out is how a key ends up unused. */

const BASE = `${window.location.origin}/api`;

const ENDPOINTS = [
  ["GET", "/v1/evidence/capabilities", "What the API serves, and what it withholds. Self-describing, so an agent can discover the surface without reading this page."],
  ["GET", "/v1/evidence/coverage", "Which companies Loom has read, and how deeply."],
  ["GET", "/v1/evidence/{ticker}", "The full packet: findings, changes, contradictions, disclosure volume, peer ranks, dependents."],
  ["GET", "/v1/evidence/{ticker}/findings", "Extracted disclosures with verbatim quotes and provenance."],
  ["GET", "/v1/evidence/{ticker}/changes", "Paragraphs added to and withdrawn from a filing against its predecessor."],
] as const;

const MCP_CONFIG = `{
  "mcpServers": {
    "loom": {
      "command": "/path/to/loom/mcp-server/.venv/bin/python",
      "args": ["/path/to/loom/mcp-server/loom_mcp.py"],
      "env": {
        "LOOM_API_KEY": "loom_sk_...",
        "LOOM_API_URL": "${window.location.origin}"
      }
    }
  }
}`;

function Copy({ text, label = "copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="copy-btn"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        } catch {
          // Blocked clipboard permission, or an insecure origin. The text is
          // on screen and selectable either way, so this fails quietly rather
          // than raising an error about a convenience.
        }
      }}
    >
      {done ? "copied" : label}
    </button>
  );
}

const shortDate = (iso: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });
};

export function ApiPage() {
  const keys = useApiKeys();
  const create = useCreateApiKey();
  const revoke = useRevokeApiKey();

  const [name, setName] = useState("");
  const [issued, setIssued] = useState<ApiKeyCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Revoking cannot be undone, so the button arms before it fires. An inline
  // second click rather than a modal: the row being revoked stays visible,
  // which a dialog covering the table does not manage.
  const [arming, setArming] = useState<string | null>(null);

  const rows = keys.data ?? [];
  const live = rows.filter((k) => !k.revoked_at);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setError(null);
    create.mutate(trimmed, {
      onSuccess: (key) => {
        setIssued(key);
        setName("");
      },
      onError: (err: unknown) => {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail;
        setError(detail ?? "The key could not be issued.");
      },
    });
  }

  return (
    <div>
      <header className="today-head">
        <h1>API access</h1>
        <p className="today-sub">
          Loom's evidence, served to programs. Everything a company packet contains
          is here — findings with their verbatim quotes, what changed against the
          previous filing, where Loom's own sources disagree, peer ranks and
          coverage — with one deliberate omission: the API does not serve Loom's
          verdict. Keys are read-only by construction and cannot reach a route that
          writes.
        </p>
      </header>

      <div className="grid grid-main-side">
        <div className="stack">
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Your keys</span>
              <span className="faint" style={{ fontSize: 9 }}>
                {live.length} active{rows.length > live.length ? ` · ${rows.length - live.length} revoked` : ""}
              </span>
            </div>

            <form className="key-form" onSubmit={submit}>
              <input
                className="search-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="what is this key for?"
                maxLength={80}
                aria-label="Key name"
              />
              <button className="key-submit" type="submit" disabled={create.isPending || !name.trim()}>
                {create.isPending ? "Issuing…" : "Issue key"}
              </button>
            </form>
            {error && <p className="key-error" role="alert">{error}</p>}

            {/* Shown once. Loom stores a digest, so this is not recoverable and
                the page says so before the reader navigates away from it. */}
            {issued && (
              <div className="key-reveal enter" role="status">
                <div className="key-reveal-head">
                  <span>{issued.name}</span>
                  <Copy text={issued.key} />
                </div>
                <code className="key-plaintext mono">{issued.key}</code>
                <p>
                  This is the only time Loom can show it. Only a digest is stored, so
                  a key that is lost has to be replaced rather than recovered.
                </p>
                <button className="key-dismiss" onClick={() => setIssued(null)}>
                  done
                </button>
              </div>
            )}

            {rows.length === 0 ? (
              <p className="empty-state" style={{ padding: "10px" }}>
                No keys yet. Issue one above to call the API or run the MCP server.
              </p>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Prefix</th>
                    <th>Issued</th>
                    <th>Last used</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((k) => (
                    <tr key={k.id} className={k.revoked_at ? "is-revoked" : undefined}>
                      <td>{k.name}</td>
                      <td className="mono dim">{k.prefix}…</td>
                      <td className="mono dim">{shortDate(k.created_at)}</td>
                      {/* Never used is a different fact from used long ago, and
                          it is the one that tells you a key can be revoked
                          without breaking anything. */}
                      <td className="mono dim">{k.last_used_at ? shortDate(k.last_used_at) : "never"}</td>
                      <td className="num">
                        {k.revoked_at ? (
                          <span className="faint" style={{ fontSize: 9 }}>revoked</span>
                        ) : (
                          <button
                            className={`revoke-btn${arming === k.id ? " is-arming" : ""}`}
                            onClick={() => (arming === k.id ? revoke.mutate(k.id, { onSettled: () => setArming(null) }) : setArming(k.id))}
                            onBlur={() => setArming((a) => (a === k.id ? null : a))}
                          >
                            {arming === k.id ? "confirm" : "revoke"}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="panel">
            <div className="panel-head"><span className="panel-title">Endpoints</span></div>
            <table className="data-table">
              <tbody>
                {ENDPOINTS.map(([verb, path, what]) => (
                  <tr key={path}>
                    <td className="mono" style={{ width: 34, verticalAlign: "top" }}>{verb}</td>
                    <td style={{ verticalAlign: "top" }}>
                      <div className="mono endpoint-path">{path}</div>
                      <div className="endpoint-what">{what}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="panel-note">
              Every endpoint accepts <code className="mono">as_of=YYYY-MM-DD</code> and
              returns only what was knowable on that date. Restatements resolve to the
              figure on file at the time and prices are read strictly backwards, so a
              packet dated last March is what Loom could have told you last March.
            </p>
          </div>
        </div>

        <div className="stack">
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Base URL</span>
              <Copy text={BASE} />
            </div>
            <code className="code-block mono">{BASE}</code>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">One request</span>
              <Copy text={`curl -H "Authorization: Bearer loom_sk_..." ${BASE}/v1/evidence/MSFT`} />
            </div>
            <code className="code-block mono">
              curl -H "Authorization: Bearer loom_sk_..." \{"\n"}
              {"  "}{BASE}/v1/evidence/MSFT
            </code>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">MCP server</span>
              <Copy text={MCP_CONFIG} />
            </div>
            <code className="code-block mono">{MCP_CONFIG}</code>
            <p className="panel-note">
              Five read-only tools for Claude Desktop, Claude Code and Cursor. Both
              paths must be absolute: these clients do not run from the project
              directory and do not inherit a shell PATH.
            </p>
          </div>

          <div className="panel">
            <div className="panel-head"><span className="panel-title">Why no verdict</span></div>
            <p className="panel-note">
              In Loom's own reader benchmark, agents given the evidence plus the
              verdict scored 10.10 points below agents given the same evidence alone
              (p=0.005). They used 29% fewer of the underlying findings and grew more
              confident while getting less accurate. Evidence without a verdict was
              the best result in the grid, so that is what this API serves. The
              verdict is not filtered out at the edge; the module that produces it is
              never imported by these routes.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
