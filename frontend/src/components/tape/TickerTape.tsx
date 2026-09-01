import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTape } from "../../hooks/useTape";
import type { TapeItem } from "../../types/models";

// Scroll speed. Comfortably readable for a short headline, while still cycling
// a full watchlist's feed in a few minutes: slower and most items are never
// seen at all, faster and the strip is decoration.
const PIXELS_PER_SECOND = 60;

// Below this many items the track is too short to fill the viewport twice, and
// the loop would show a gap. Repeating the list covers it.
const MIN_ITEMS_FOR_LOOP = 6;

/** Whether the reader has asked for less motion.
 *
 *  Read in JS as well as CSS because the two halves of the fix are different:
 *  the stylesheet stops the movement, but the track also renders the item list
 *  twice to hide the loop seam, and a strip that is not moving has no seam to
 *  hide. Left alone, a reduced-motion reader would get a static list with every
 *  headline in it twice. */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );

  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!query) return;
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

function relativeTime(iso: string | null): string | null {
  if (!iso) return null;
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 0) return null;
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function chipClass(tone: TapeItem["tone"]): string {
  switch (tone) {
    case "urgent": return "tape-chip tape-chip-urgent";
    case "accent": return "tape-chip tape-chip-accent";
    case "positive": return "tape-chip tape-chip-positive";
    case "negative": return "tape-chip tape-chip-negative";
    default: return "tape-chip";
  }
}

function TapeEntry({ item, onOpen }: { item: TapeItem; onOpen: (item: TapeItem) => void }) {
  const age = item.kind === "news" ? relativeTime(item.occurred_at) : null;
  return (
    <a
      className="tape-item"
      href={item.href ?? `/companies/${item.ticker}`}
      onClick={(event) => {
        // Modified clicks and middle clicks keep their native meaning, so a
        // reader can open a headline in a new tab the way they expect.
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        onOpen(item);
      }}
      {...(item.href ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      <span className={chipClass(item.tone)}>{item.label}</span>
      <span className="ticker-symbol">{item.ticker}</span>
      <span>{item.headline}</span>
      {item.detail && <span className="tape-detail">{item.detail}</span>}
      {age && <span className="tape-time">{age} ago</span>}
    </a>
  );
}

/** The running feed across the top of the dashboard.
 *
 *  Contents come from the watchlist, so the tape reflects whatever the reader
 *  actually tracks: add a ticker and it appears here on the next refresh,
 *  with nothing in this file naming a company.
 *
 *  Two details are doing real work. The track holds the item list twice and
 *  animates to exactly -50%, which is what makes the loop seamless rather than
 *  snapping back at the end. And the duration is computed from the measured
 *  width instead of being a fixed number of seconds, because a fixed duration
 *  means the strip crawls on a quiet day and races on a busy one, which is the
 *  usual reason these things end up unreadable.
 */
export function TickerTape() {
  const { data, isLoading } = useTape();
  const reducedMotion = usePrefersReducedMotion();
  const navigate = useNavigate();
  const trackRef = useRef<HTMLDivElement>(null);
  const [duration, setDuration] = useState(0);

  const items = data ?? [];

  // Repeated until there is enough content to cover the viewport twice over,
  // otherwise a short feed leaves visible dead space mid-cycle.
  let loop = items;
  if (items.length > 0 && items.length < MIN_ITEMS_FOR_LOOP) {
    const times = Math.ceil(MIN_ITEMS_FOR_LOOP / items.length);
    loop = Array.from({ length: times }, () => items).flat();
  }

  useLayoutEffect(() => {
    const track = trackRef.current;
    if (!track || reducedMotion) return;
    // scrollWidth covers both copies; one cycle is half of it.
    const cycle = track.scrollWidth / 2;
    setDuration(cycle > 0 ? cycle / PIXELS_PER_SECOND : 0);
  }, [loop.length, data, reducedMotion]);

  // Re-measure when the window resizes: the strip is full width, so its
  // content width (and therefore the correct duration) changes with it.
  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    if (reducedMotion) return;
    const observer = new ResizeObserver(() => {
      const cycle = track.scrollWidth / 2;
      setDuration(cycle > 0 ? cycle / PIXELS_PER_SECOND : 0);
    });
    observer.observe(track);
    return () => observer.disconnect();
  }, [reducedMotion]);

  if (isLoading || items.length === 0) return null;

  const open = (item: TapeItem) => {
    if (item.href) window.open(item.href, "_blank", "noopener,noreferrer");
    else navigate(`/companies/${item.ticker}`);
  };

  const earningsCount = items.filter((i) => i.kind === "earnings").length;

  return (
    <div className="tape">
      <div className="tape-legend">
        <span className="live-dot" />
        <span>{earningsCount > 0 ? `${earningsCount} reporting` : "live"}</span>
      </div>
      <div className="tape-viewport">
        <div
          className="tape-track"
          ref={trackRef}
          style={duration ? { animationDuration: `${duration}s` } : undefined}
        >
          {/* Rendered twice while moving: the second copy is what is on screen
              as the first scrolls out, and it is why the loop has no seam.
              Once when the reader has asked for less motion, since a static
              strip has no seam and would just repeat itself. */}
          {(reducedMotion ? [0] : [0, 1]).map((copy) =>
            loop.map((item, index) => (
              <TapeEntry key={`${copy}-${index}-${item.ticker}`} item={item} onOpen={open} />
            )),
          )}
        </div>
      </div>
    </div>
  );
}
