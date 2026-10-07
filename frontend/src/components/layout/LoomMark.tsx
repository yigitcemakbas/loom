/** The Loom mark: three warp threads, three weft, woven.
 *
 *  Drawn rather than embedded. A raster logo would need three sizes, would sit
 *  at a fixed colour against a theme that has a light and a dark reading, and
 *  would blur on the favicon. The mark is six straight lines, so SVG is both
 *  smaller and sharper.
 *
 *  Two deliberate departures from the supplied artwork.
 *
 *  The navy is `currentColor`. Navy on this near-black background is close to
 *  invisible, and the mark needs to work in the header, the sign-in screen and
 *  the browser tab, which are three different backgrounds. Inheriting colour
 *  lets each placement decide.
 *
 *  The green and red are the theme's own `--positive` and `--negative`. That is
 *  not a colour match for convenience: the design system's rule is that green
 *  and red mean direction and are never decoration, and the logo happens to put
 *  exactly one green thread and one red thread through a neutral weave. Using
 *  the semantic tokens keeps the one place colour is decorative honest about
 *  borrowing the vocabulary. */

interface Props {
  size?: number;
  /** Render the two accent threads in the neutral colour. For places where the
   *  mark sits beside live data and a stray green could read as a value. */
  monochrome?: boolean;
  title?: string;
}

// Thread positions on a 100-unit grid. Equal spacing, with the ends overhanging
// the outermost crossing so the weave reads as fabric rather than as a grid of
// boxes — the overhang is what makes it a loom and not a window.
const WARP = [30, 50, 70]; // verticals
const WEFT = [30, 50, 70]; // horizontals

// Measured off the supplied artwork rather than guessed. There the bars are
// 20px thick on a 53px grid pitch, a ratio of 0.38; the first version of this
// used 10 on a pitch of 20, which is 0.50 and read as a blocky hash instead of
// a weave. On this 100-unit grid the pitch is 20, so the thread is 7.5.
const WIDTH = 7.5;

// The two accent threads run longer than the four neutral ones, which is in the
// artwork and is the detail that makes it a loom: the coloured threads are the
// ones being drawn through. Green spans 212px against the navy verticals' ~180,
// so it overhangs by about half a pitch more at each end.
const SPAN = { start: 11, end: 89 };
const ACCENT_SPAN = { start: 6, end: 94 };

// Flat ends. The artwork's are barely radiused and this interface's own rule is
// that nothing has rounded corners, so they are cut square.
const CAP = "butt" as const;

export function LoomMark({ size = 20, monochrome = false, title }: Props) {
  const neutral = "currentColor";
  const green = monochrome ? neutral : "var(--positive)";
  const red = monochrome ? neutral : "var(--negative)";

  // Over-under, decided by parity of the crossing. A fabric alternates at every
  // intersection; picking a rule rather than hand-placing nine crossings means
  // the weave stays correct if the thread count ever changes.
  const horizontalIsOver = (col: number, row: number) => (col + row) % 2 === 0;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      role={title ? "img" : "presentation"}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      {/* Weft first, then warp over it, then the weft segments that belong on
          top. Three passes is what produces an interlace from straight lines. */}
      {WEFT.map((y, row) => (
        <line
          key={`weft-${y}`}
          x1={row === 1 ? ACCENT_SPAN.start : SPAN.start} y1={y}
          x2={row === 1 ? ACCENT_SPAN.end : SPAN.end} y2={y}
          stroke={row === 1 ? red : neutral}
          strokeWidth={WIDTH} strokeLinecap={CAP}
        />
      ))}

      {WARP.map((x, col) => (
        <line
          key={`warp-${x}`}
          x1={x} y1={col === 1 ? ACCENT_SPAN.start : SPAN.start}
          x2={x} y2={col === 1 ? ACCENT_SPAN.end : SPAN.end}
          stroke={col === 1 ? green : neutral}
          strokeWidth={WIDTH} strokeLinecap={CAP}
        />
      ))}

      {WEFT.flatMap((y, row) =>
        WARP.map((x, col) =>
          horizontalIsOver(col, row) ? (
            <line
              key={`over-${x}-${y}`}
              x1={x - WIDTH} y1={y} x2={x + WIDTH} y2={y}
              stroke={row === 1 ? red : neutral}
              strokeWidth={WIDTH}
            />
          ) : null,
        ),
      )}
    </svg>
  );
}

/** The mark with the wordmark beside it, for the header and the sign-in screen.
 *
 *  The wordmark is sized from the mark rather than from the surrounding rule.
 *  Both placements sit inside containers with their own font-size and
 *  letter-spacing, so an inherited wordmark came out a different size in each
 *  and the lockup's proportions changed with its surroundings. Deriving it here
 *  means the logo looks the same wherever it is dropped. */
export function LoomLogo({ size = 20, subtitle }: { size?: number; subtitle?: string }) {
  return (
    <span className="loom-logo">
      <LoomMark size={size} title="Loom" />
      <span className="loom-logo-text">
        <span className="loom-wordmark" style={{ fontSize: Math.round(size * 0.72) }}>
          LOOM
        </span>
        {subtitle && (
          <span className="loom-subtitle" style={{ fontSize: Math.max(7, Math.round(size * 0.26)) }}>
            {subtitle}
          </span>
        )}
      </span>
    </span>
  );
}
