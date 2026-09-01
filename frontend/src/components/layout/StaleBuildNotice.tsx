import { useCallback, useEffect, useRef, useState } from "react";

// A background check, not a poll for its own sake: the common way to notice a
// new build is to come back to a tab that has been sitting open, so focus is
// the trigger that matters and the interval is only a backstop.
const CHECK_INTERVAL_MS = 5 * 60 * 1000;
const MIN_GAP_BETWEEN_CHECKS_MS = 60 * 1000;

/** Fingerprint of the asset URLs a document loads.
 *
 *  Vite content-hashes these filenames, so the set of URLs changes if and only
 *  if the build changed. Comparing them is therefore an exact test with nothing
 *  to keep in sync: no version constant to bump, no endpoint to remember to
 *  update. In dev the filenames are unhashed and stable, so this compares equal
 *  and the check quietly does nothing. */
function fingerprint(doc: Document): string {
  const assets = [
    ...Array.from(doc.querySelectorAll("script[src]"), (el) => el.getAttribute("src")),
    ...Array.from(doc.querySelectorAll('link[rel="stylesheet"]'), (el) => el.getAttribute("href")),
  ];
  return assets.filter(Boolean).sort().join("|");
}

/** Tells the reader when the page they are looking at is running code the
 *  server has already replaced.
 *
 *  This exists because of a failure that is invisible from the inside. A
 *  single-page app holds its JavaScript for the life of the tab, so a
 *  redeploy reaches nobody who already has it open. The page keeps working,
 *  which is the problem: there is no error, no blank screen, nothing to
 *  suggest anything is wrong. A fix that shipped hours ago simply appears not
 *  to have been made, and the natural conclusion is that it was not.
 *
 *  Reloading is left to the reader rather than done automatically. A refresh
 *  fired without asking would discard an unsaved note mid-sentence. */
export function StaleBuildNotice() {
  const [stale, setStale] = useState(false);
  const loadedRef = useRef<string>("");
  const lastCheckRef = useRef(0);

  useEffect(() => {
    loadedRef.current = fingerprint(document);
  }, []);

  const check = useCallback(async () => {
    if (stale || !loadedRef.current) return;

    const now = Date.now();
    if (now - lastCheckRef.current < MIN_GAP_BETWEEN_CHECKS_MS) return;
    lastCheckRef.current = now;

    try {
      // no-store, or this request is answered by the very cache whose
      // staleness it is trying to detect.
      const response = await fetch(`${import.meta.env.BASE_URL || "/"}`, { cache: "no-store" });
      if (!response.ok) return;
      const served = fingerprint(
        new DOMParser().parseFromString(await response.text(), "text/html"),
      );
      if (served && served !== loadedRef.current) setStale(true);
    } catch {
      // Offline, or the server is restarting mid-deploy. Neither is worth
      // telling the reader about; the next check will settle it.
    }
  }, [stale]);

  useEffect(() => {
    // Two triggers rather than one, and deliberately not gated on the same
    // condition. `visibilitychange` fires for a tab being switched back to and
    // is only meaningful when the tab became visible. `focus` fires when the
    // window itself is returned to and already implies someone is looking, so
    // re-testing visibilityState there adds nothing and silently disables the
    // check anywhere that API reports "hidden" for an on-screen page, which
    // some embedded and remote-rendered browsers do.
    const onVisibility = () => {
      if (document.visibilityState === "visible") void check();
    };
    const onFocus = () => void check();

    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onFocus);
    const timer = window.setInterval(() => void check(), CHECK_INTERVAL_MS);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onFocus);
      window.clearInterval(timer);
    };
  }, [check]);

  if (!stale) return null;

  return (
    <div
      className="notice"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "5px 10px",
        marginBottom: 8,
        border: "1px solid var(--accent-dim)",
        background: "var(--bg-inset)",
        color: "var(--accent)",
      }}
    >
      <span>A newer build of Loom is available. This tab is still running the old one.</span>
      <button className="btn" onClick={() => window.location.reload()}>reload</button>
    </div>
  );
}
