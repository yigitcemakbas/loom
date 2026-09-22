import { useState } from "react";
import { useEffect } from "react";
import { checkUsername, requestCode, signInWithPassword, signUp, verifyCode } from "../api/auth";
import { useAuth } from "./AuthContext";

type Mode = "signin" | "signup" | "verify";

/** The front door.
 *
 *  Three states on one surface rather than three screens. Verification needs
 *  the email from the step before it to stay visible, because somebody reading
 *  a code off their phone should be able to see which address it went to
 *  without navigating back and losing it.
 *
 *  The left panel is not decoration. A signed-out person has no other way to
 *  learn what this is, and a bare form on a black page tells them nothing.
 */
export function SignIn() {
  const { signIn } = useAuth();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [username, setUsername] = useState("");
  const [usernameNote, setUsernameNote] = useState<string | null>(null);
  const [usernameOk, setUsernameOk] = useState(false);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Checked as it is typed, debounced. A username is public by construction,
  // so saying one is taken discloses nothing, and the alternative is a form
  // that refuses a name at submit time with no way to find a free one.
  useEffect(() => {
    if (mode !== "signup") return;
    const candidate = username.trim();
    if (!candidate) { setUsernameNote(null); setUsernameOk(false); return; }

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const result = await checkUsername(candidate);
        if (cancelled) return;
        setUsernameOk(result.available);
        setUsernameNote(result.available ? "Available." : result.problem);
      } catch {
        if (!cancelled) { setUsernameOk(false); setUsernameNote(null); }
      }
    }, 350);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [username, mode]);

  function reset(next: Mode) {
    setMode(next);
    setError(null);
    setNote(null);
    setCode("");
  }

  async function onSignIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const { token } = await signInWithPassword(identifier.trim(), password);
      await signIn(token);
    } catch (err: unknown) {
      // 403 means the password was right and the address is unconfirmed, so
      // the useful next step is the code screen rather than an error the
      // person cannot act on.
      if (status(err) === 403) {
        // The password was right and the address is unconfirmed, so the useful
        // next step is the code screen. Needs an address, which a username
        // sign-in does not supply, so it is only offered when one was typed.
        const typed = identifier.trim();
        if (typed.includes("@")) {
          setEmail(typed);
          try { await requestCode(typed); } catch { /* shown below */ }
          setNote("Confirm your email address to finish setting up your account.");
          setMode("verify");
        } else {
          setError("Confirm your email address first. Sign in with your email to get a new code.");
        }
      } else {
        setError(messageFrom(err, "That email or password is not right."));
      }
    } finally {
      setBusy(false);
    }
  }

  async function onSignUp(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const result = await signUp(email.trim(), username.trim(), password);
      setNote(result.message);
      setMode("verify");
    } catch (err: unknown) {
      setError(messageFrom(err, "Could not create that account."));
    } finally {
      setBusy(false);
    }
  }

  async function onVerify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const { token } = await verifyCode(email.trim(), code.trim());
      await signIn(token);
    } catch (err: unknown) {
      setError(messageFrom(err, "That code is not valid. Request a new one."));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true); setError(null);
    try {
      const result = await requestCode(email.trim());
      setNote(result.message);
    } catch (err: unknown) {
      setError(messageFrom(err, "Could not send a new code."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <section className="auth-pitch">
        <div className="auth-brand">LOOM</div>
        <h1 className="auth-headline">
          The filings, read.
        </h1>
        <p className="auth-sub">
          Loom reads annual reports, earnings calls, insider filings and news for
          130 companies, scores every one of them against its peers on measures
          drawn from their own accounts, and tells you the part that should change
          your mind.
        </p>
        <ul className="auth-points">
          <li>
            <strong>Evidence, not opinion.</strong> Every claim carries the sentence
            it came from.
          </li>
          <li>
            <strong>It says when it disagrees with itself.</strong> Confident
            language over deteriorating cash is a finding, not an average.
          </li>
          <li>
            <strong>It says when it does not know.</strong> A company Loom has not
            read is reported as unread, never as calm.
          </li>
        </ul>
      </section>

      <section className="auth-panel">
        <div className="auth-card">
          {mode !== "verify" && (
            <div className="auth-tabs" role="tablist">
              <button
                role="tab"
                aria-selected={mode === "signin"}
                className={mode === "signin" ? "active" : ""}
                onClick={() => reset("signin")}
              >
                Sign in
              </button>
              <button
                role="tab"
                aria-selected={mode === "signup"}
                className={mode === "signup" ? "active" : ""}
                onClick={() => reset("signup")}
              >
                Create account
              </button>
            </div>
          )}

          {mode === "signin" && (
            <form onSubmit={onSignIn}>
              <Field label="Username or email">
                <input className="auth-input" value={identifier} autoFocus
                  autoComplete="username" placeholder="yourname"
                  onChange={(e) => setIdentifier(e.target.value)} required />
              </Field>
              <Field label="Password">
                <input className="auth-input" type="password" value={password}
                  autoComplete="current-password" placeholder="••••••••••"
                  onChange={(e) => setPassword(e.target.value)} required />
              </Field>
              <button className="auth-btn" type="submit" disabled={busy || !identifier || !password}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>
          )}

          {mode === "signup" && (
            <form onSubmit={onSignUp}>
              <Field label="Username" hint={usernameNote ?? "Letters, numbers, underscores and hyphens."}>
                <input className={`auth-input ${usernameNote && !usernameOk ? "invalid" : ""}`}
                  value={username} autoFocus autoComplete="username" placeholder="yourname"
                  onChange={(e) => setUsername(e.target.value)} required />
              </Field>
              <Field label="Email">
                <input className="auth-input" type="email" value={email}
                  autoComplete="email" placeholder="you@example.com"
                  onChange={(e) => setEmail(e.target.value)} required />
              </Field>
              <Field label="Password" hint="At least 10 characters. A phrase beats a puzzle.">
                <input className="auth-input" type="password" value={password}
                  autoComplete="new-password" placeholder="••••••••••"
                  onChange={(e) => setPassword(e.target.value)} required minLength={10} />
              </Field>
              <button className="auth-btn" type="submit"
                disabled={busy || !email || !usernameOk || password.length < 10}>
                {busy ? "Creating…" : "Create account"}
              </button>
              <p className="auth-fine">
                We send a six digit code to confirm the address is yours. Free, and
                your email is only ever used to sign you in.
              </p>
            </form>
          )}

          {mode === "verify" && (
            <form onSubmit={onVerify}>
              <div className="auth-verify-head">
                <span className="auth-verify-label">Confirm your email</span>
                <span className="auth-verify-email">{email.trim()}</span>
              </div>
              <input className="auth-input auth-code" value={code} autoFocus
                inputMode="numeric" autoComplete="one-time-code" placeholder="000000"
                maxLength={6}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required />
              <button className="auth-btn" type="submit" disabled={busy || code.length < 6}>
                {busy ? "Checking…" : "Confirm and sign in"}
              </button>
              <div className="auth-links">
                <button type="button" className="auth-link" onClick={resend} disabled={busy}>
                  Send another code
                </button>
                <button type="button" className="auth-link" onClick={() => reset("signin")}>
                  Back
                </button>
              </div>
            </form>
          )}

          {note && <p className="auth-note">{note}</p>}
          {error && <p className="auth-error">{error}</p>}
        </div>
      </section>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="auth-field">
      <span className="auth-label">{label}</span>
      {children}
      {hint && <span className="auth-hint">{hint}</span>}
    </label>
  );
}

function status(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status;
}

function messageFrom(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}
