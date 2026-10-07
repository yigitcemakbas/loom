import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import type { ExposureGraph, ExposureNode } from "../../types/models";

interface Props {
  graph: ExposureGraph;
  stanceByTicker: Map<string, string>;
  selected: string | null;
  onSelect: (ticker: string | null) => void;
}

interface Placed extends ExposureNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

/** Where the camera is looking: a scale and a screen-space offset. */
interface Camera {
  k: number;
  tx: number;
  ty: number;
}

// The layout runs in its own fixed coordinate space, not in the panel's pixels.
// That separation is the whole reason this view can be resized at all: the
// simulation is O(n²) per iteration and the panel's width changes on every
// frame of a window drag, so laying out in screen coordinates meant re-running
// 3.5 million force calculations while the user was still dragging. The graph
// is now laid out once and the camera fits it to whatever space exists.
const LOGICAL_W = 1000;
const LOGICAL_H = 640;

// Enough iterations for clusters to separate, few enough to run once on load
// without blocking. The layout is deterministic given the same graph, so the
// picture does not rearrange itself between visits, which matters: a reader
// learns where things are.
const ITERATIONS = 420;
const REPULSION = 3400;
const SPRING = 0.016;
const DAMPING = 0.82;

// Without this the simulation is only repulsive plus a few springs, so any
// node with no edges is pushed outward by everything and pulled back by
// nothing. It ends pinned to the border, and enough of them do that the graph
// reads as a rectangle of debris around an empty middle. Gravity gives every
// node somewhere to fall back to.
const GRAVITY = 0.0055;

// Repulsion was previously ignored past a cutoff, which saved work and meant
// distant clusters never pushed each other apart, so they piled into the
// corners. Applied everywhere, with a floor so coincident nodes do not produce
// an infinite force.
const MIN_DISTANCE = 12;

const MIN_ZOOM = 0.3;
const MAX_ZOOM = 9;

// Screen pixels kept clear around a fitted graph, so nodes at the edge of the
// bounding box are not cut in half by the panel border.
const FIT_PAD = 46;

// A press that moves less than this is a click, not a drag. Without it, the
// hand tremor between pointerdown and pointerup on a node reads as a pan and
// the selection never fires.
const DRAG_SLOP = 4;

// Labels hold this size on screen at every zoom level. Letting them scale with
// the camera means they are unreadable when zoomed out and absurd when zoomed
// in, which defeats the purpose of zooming to read them.
const LABEL_PX = 9;

const CAMERA_MS = 420;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

const reduceMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function stanceColour(stance: string | undefined): string {
  if (!stance) return "var(--text-faint)";
  if (stance.includes("positive")) return "var(--positive)";
  if (stance.includes("negative")) return "var(--negative)";
  if (stance === "mixed") return "var(--accent)";
  return "var(--text-faint)";
}

/** Deterministic seed, so the same graph always lays out the same way. */
function seeded(i: number, total: number, w: number, h: number) {
  const angle = (i / total) * Math.PI * 2;
  const radius = Math.min(w, h) * 0.34;
  return {
    x: w / 2 + Math.cos(angle) * radius,
    y: h / 2 + Math.sin(angle) * radius,
  };
}

const radiusOf = (reach: number) => 3.4 + Math.sqrt(reach) * 2.6;

interface Box { minX: number; minY: number; maxX: number; maxY: number }

/** The extent of a set of nodes, including the circles themselves rather than
 *  just their centres — fitting to centres clips the largest hubs, which are
 *  precisely the ones worth seeing. */
function boxOf(set: Placed[]): Box | null {
  if (set.length === 0) return null;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of set) {
    const r = radiusOf(n.reach) + 10;
    minX = Math.min(minX, n.x - r);
    minY = Math.min(minY, n.y - r);
    maxX = Math.max(maxX, n.x + r);
    maxY = Math.max(maxY, n.y + r);
  }
  return { minX, minY, maxX, maxY };
}

function fitTo(box: Box, w: number, h: number): Camera {
  const bw = Math.max(1, box.maxX - box.minX);
  const bh = Math.max(1, box.maxY - box.minY);
  const k = clamp(
    Math.min((w - FIT_PAD * 2) / bw, (h - FIT_PAD * 2) / bh),
    MIN_ZOOM,
    MAX_ZOOM,
  );
  return {
    k,
    tx: w / 2 - k * (box.minX + bw / 2),
    ty: h / 2 - k * (box.minY + bh / 2),
  };
}

/** The dependency graph, drawn, pannable and zoomable.
 *
 *  This replaced a sortable table, and the reason is the product's whole
 *  premise. A table of a hundred and twenty rows hands a reader the raw
 *  material and leaves the synthesis to them, which is the job Loom exists to
 *  do. The relationship between companies is the one thing in this database
 *  that genuinely cannot be read off a row: that an event at Marvell reaches
 *  seven semiconductor names, or that Palo Alto sits at the centre of a
 *  security cluster, is visible in a diagram in about a second and invisible
 *  in a spreadsheet at any length.
 *
 *  Selecting a node dims everything it does not touch and moves the camera to
 *  frame what it does. The camera move is not decoration: selections also
 *  arrive from the table beside the map, and without it clicking a row there
 *  changed some pixels in a part of the diagram the reader was not looking at.
 *  The movement is what connects the name to its position.
 *
 *  The drawing itself is fixed. Zoom and pan are a camera over a layout that
 *  never moves, so the picture a reader learns stays learnt. */
export function PropagationMap({ graph, stanceByTicker, selected, onSelect }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [nodes, setNodes] = useState<Placed[]>([]);
  const [cam, setCam] = useState<Camera>({ k: 1, tx: 0, ty: 0 });

  const frame = useRef(0);
  const camRef = useRef(cam);
  // Set once the reader has taken the camera themselves. A resize then leaves
  // their view alone instead of yanking it back to the default framing.
  const userMoved = useRef(false);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const swallowClick = useRef(false);

  const edges = graph.edges;

  useEffect(() => {
    camRef.current = cam;
  }, [cam]);

  // The panel decides the size, so the map fills it at any window width rather
  // than scaling one fixed drawing up and down.
  useLayoutEffect(() => {
    const el = host.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const r = entry.contentRect;
      setSize({ w: Math.round(r.width), h: Math.round(r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (graph.nodes.length === 0) return;

    const placed: Placed[] = graph.nodes.map((n, i) => ({
      ...n,
      ...seeded(i, graph.nodes.length, LOGICAL_W, LOGICAL_H),
      vx: 0,
      vy: 0,
    }));
    const index = new Map(placed.map((n, i) => [n.ticker, i]));

    for (let step = 0; step < ITERATIONS; step++) {
      // Repulsion: every node pushes every other apart, which is what stops
      // the whole graph collapsing into one unreadable knot.
      for (let i = 0; i < placed.length; i++) {
        for (let j = i + 1; j < placed.length; j++) {
          const a = placed[i];
          const b = placed[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < MIN_DISTANCE) dist = MIN_DISTANCE;
          const force = REPULSION / (dist * dist);
          dx /= dist;
          dy /= dist;
          a.vx += dx * force;
          a.vy += dy * force;
          b.vx -= dx * force;
          b.vy -= dy * force;
        }
      }

      // Attraction along edges: connected companies are pulled together, which
      // is what makes sectors emerge as clusters rather than being assigned.
      for (const edge of edges) {
        const ai = index.get(edge.hub);
        const bi = index.get(edge.dependent);
        if (ai === undefined || bi === undefined) continue;
        const a = placed[ai];
        const b = placed[bi];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        a.vx += dx * SPRING;
        a.vy += dy * SPRING;
        b.vx -= dx * SPRING;
        b.vy -= dy * SPRING;
      }

      for (const n of placed) {
        // Pull toward the middle, proportional to distance from it.
        n.vx += (LOGICAL_W / 2 - n.x) * GRAVITY;
        n.vy += (LOGICAL_H / 2 - n.y) * GRAVITY;

        n.vx *= DAMPING;
        n.vy *= DAMPING;
        n.x = Math.max(24, Math.min(LOGICAL_W - 24, n.x + n.vx));
        n.y = Math.max(24, Math.min(LOGICAL_H - 24, n.y + n.vy));
      }
    }

    setNodes(placed);
  }, [graph, edges]);

  const byTicker = useMemo(() => new Map(nodes.map((n) => [n.ticker, n])), [nodes]);

  // What the selection touches, in both directions: what this company reaches,
  // and what reaches it.
  const connected = useMemo(() => {
    if (!selected) return null;
    const set = new Set<string>([selected]);
    for (const e of edges) {
      if (e.hub === selected) set.add(e.dependent);
      if (e.dependent === selected) set.add(e.hub);
    }
    return set;
  }, [selected, edges]);

  /** Move the camera, either at once or over CAMERA_MS.
   *
   *  Scale is interpolated in log space and the viewport centre linearly,
   *  rather than interpolating tx/ty directly: a straight line through the
   *  offsets while the scale changes swings the picture sideways on the way,
   *  because the offsets mean different distances at different zooms. */
  const moveTo = useCallback((target: Camera, animate: boolean) => {
    cancelAnimationFrame(frame.current);
    const { w, h } = size;
    const from = camRef.current;
    if (!animate || reduceMotion() || !w || !h) {
      camRef.current = target;
      setCam(target);
      return;
    }

    const c0 = { x: (w / 2 - from.tx) / from.k, y: (h / 2 - from.ty) / from.k };
    const c1 = { x: (w / 2 - target.tx) / target.k, y: (h / 2 - target.ty) / target.k };
    const lk0 = Math.log(from.k);
    const lk1 = Math.log(target.k);
    const start = performance.now();

    const step = (now: number) => {
      const t = Math.min(1, (now - start) / CAMERA_MS);
      const e = 1 - Math.pow(1 - t, 3);
      const k = Math.exp(lk0 + (lk1 - lk0) * e);
      const cx = c0.x + (c1.x - c0.x) * e;
      const cy = c0.y + (c1.y - c0.y) * e;
      const next = { k, tx: w / 2 - k * cx, ty: h / 2 - k * cy };
      camRef.current = next;
      setCam(next);
      if (t < 1) frame.current = requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
  }, [size]);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const wholeBox = useMemo(() => boxOf(nodes), [nodes]);
  const targetBox = useMemo(() => {
    if (nodes.length === 0) return null;
    if (!connected) return wholeBox;
    const subset = nodes.filter((n) => connected.has(n.ticker));
    return boxOf(subset) ?? wholeBox;
  }, [nodes, connected, wholeBox]);

  // One effect owns the framing. It animates when the selection changed and
  // snaps otherwise, which is what makes a resize feel like a resize and a
  // click feel like a move.
  const lastSelected = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (!targetBox || !size.w || !size.h) return;
    const first = lastSelected.current === undefined;
    const changed = lastSelected.current !== selected;
    lastSelected.current = selected;
    if (!changed && userMoved.current) return;
    if (changed) userMoved.current = false;
    moveTo(fitTo(targetBox, size.w, size.h), changed && !first);
  }, [targetBox, size, selected, moveTo]);

  const zoomAbout = useCallback((factor: number, px: number, py: number) => {
    cancelAnimationFrame(frame.current);
    userMoved.current = true;
    setCam((c) => {
      const k = clamp(c.k * factor, MIN_ZOOM, MAX_ZOOM);
      const s = k / c.k;
      const next = { k, tx: px - (px - c.tx) * s, ty: py - (py - c.ty) * s };
      camRef.current = next;
      return next;
    });
  }, []);

  // Registered by hand rather than through onWheel, because React attaches
  // wheel listeners passively and a passive listener cannot preventDefault —
  // the page would scroll away underneath the zoom.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      // A trackpad pinch arrives as a wheel event with ctrlKey set and much
      // coarser deltas, so it needs its own sensitivity or one pinch crosses
      // the entire zoom range.
      const intensity = e.ctrlKey ? 0.009 : 0.0022;
      zoomAbout(Math.exp(-e.deltaY * intensity), e.clientX - rect.left, e.clientY - rect.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAbout]);

  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    // State first, capture second. setPointerCapture throws on an id the
    // browser does not recognise, and when that threw ahead of the assignment
    // the gesture was never recorded: the drag then panned nothing and the
    // click that ended it cleared the selection instead.
    drag.current = { x: e.clientX, y: e.clientY, moved: false };
    cancelAnimationFrame(frame.current);
    try {
      svgRef.current?.setPointerCapture(e.pointerId);
    } catch {
      // Capture only keeps a drag alive past the panel edge. Without it the
      // pan still works, it just stops at the boundary.
    }
  };

  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) < DRAG_SLOP) return;
    d.moved = true;
    d.x = e.clientX;
    d.y = e.clientY;
    userMoved.current = true;
    setCam((c) => {
      const next = { ...c, tx: c.tx + dx, ty: c.ty + dy };
      camRef.current = next;
      return next;
    });
  };

  const endDrag = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (drag.current?.moved) swallowClick.current = true;
    drag.current = null;
    try {
      svgRef.current?.releasePointerCapture(e.pointerId);
    } catch {
      /* see above: nothing was captured */
    }
  };

  /** A pan ends with a click event on whatever was under the pointer. Without
   *  this the gesture that moves the map also clears the selection, or picks
   *  a company the reader was only dragging past. */
  const consumedByDrag = useCallback(() => {
    if (!swallowClick.current) return false;
    swallowClick.current = false;
    return true;
  }, []);

  const resetView = useCallback(() => {
    if (!wholeBox || !size.w || !size.h) return;
    userMoved.current = false;
    moveTo(fitTo(wholeBox, size.w, size.h), true);
  }, [wholeBox, size, moveTo]);

  const onKeyDown = (e: ReactKeyboardEvent<SVGSVGElement>) => {
    const centre: [number, number] = [size.w / 2, size.h / 2];
    if (e.key === "+" || e.key === "=") zoomAbout(1.5, ...centre);
    else if (e.key === "-" || e.key === "_") zoomAbout(1 / 1.5, ...centre);
    else if (e.key === "0") resetView();
    else if (e.key === "Escape") onSelect(null);
    else return;
    e.preventDefault();
  };

  // Quantised so the memo below does not rebuild every label on every frame of
  // a camera move; the font size is counter-scaled, so it only needs to track
  // the zoom closely enough that nobody can see the steps.
  const labelScale = Math.round(cam.k * 20) / 20 || 1;

  const drawnEdges = useMemo(
    () =>
      edges.map((e, i) => {
        const a = byTicker.get(e.hub);
        const b = byTicker.get(e.dependent);
        if (!a || !b) return null;
        const lit = !connected || (connected.has(e.hub) && connected.has(e.dependent));
        return (
          <line
            key={i}
            className="map-edge"
            x1={a.x} y1={a.y} x2={b.x} y2={b.y}
            vectorEffect="non-scaling-stroke"
            stroke={lit && selected ? "var(--accent)" : "var(--border-strong)"}
            strokeWidth={lit && selected ? 1.1 : 0.5}
            opacity={connected ? (lit ? 0.85 : 0.07) : 0.34}
          />
        );
      }),
    [edges, byTicker, connected, selected],
  );

  const drawnNodes = useMemo(
    () =>
      nodes.map((n) => {
        const lit = !connected || connected.has(n.ticker);
        // Size carries reach: a company many others depend on is drawn larger,
        // because that is exactly what makes its events worth watching.
        const r = radiusOf(n.reach);
        const isSelected = n.ticker === selected;
        // More names appear as the reader zooms in. At the fitted scale only
        // the hubs can be labelled without the text colliding; once there is
        // room, withholding the names is just making them click to find out.
        const labelled =
          isSelected || n.reach >= 3 || labelScale >= 1.8 || (connected?.has(n.ticker) ?? false);
        return (
          <g
            key={n.ticker}
            className="map-node"
            opacity={lit ? 1 : 0.12}
            onClick={(event) => {
              event.stopPropagation();
              if (consumedByDrag()) return;
              onSelect(isSelected ? null : n.ticker);
            }}
          >
            <circle
              cx={n.x} cy={n.y} r={r}
              fill={stanceColour(stanceByTicker.get(n.ticker))}
              vectorEffect="non-scaling-stroke"
              stroke={isSelected ? "var(--accent)" : "var(--bg-panel)"}
              strokeWidth={isSelected ? 2 : 1}
            />
            {labelled && (
              <text
                x={n.x} y={n.y - r - 3 / labelScale}
                textAnchor="middle"
                className="map-label"
                fontSize={LABEL_PX / labelScale}
                fill={isSelected ? "var(--accent)" : "var(--text-dim)"}
              >
                {n.ticker}
              </text>
            )}
          </g>
        );
      }),
    [nodes, connected, selected, stanceByTicker, labelScale, onSelect, consumedByDrag],
  );

  const zoomPct = Math.round(cam.k * 100);

  return (
    <div className="map-host" ref={host}>
      <svg
        ref={svgRef}
        className="propagation-map"
        width={size.w || undefined}
        height={size.h || undefined}
        tabIndex={0}
        role="application"
        aria-label="Dependency map. Drag to pan, scroll to zoom, click a company to see what it reaches."
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={resetView}
        onKeyDown={onKeyDown}
        onClick={() => {
          if (consumedByDrag()) return;
          onSelect(null);
        }}
      >
        <g transform={`translate(${cam.tx} ${cam.ty}) scale(${cam.k})`}>
          {drawnEdges}
          {drawnNodes}
        </g>
      </svg>

      {nodes.length === 0 && <div className="map-overlay">Laying out the graph…</div>}

      <div className="map-controls">
        <button type="button" onClick={() => zoomAbout(1 / 1.5, size.w / 2, size.h / 2)} aria-label="Zoom out">−</button>
        <span className="map-zoom mono" aria-hidden>{zoomPct}%</span>
        <button type="button" onClick={() => zoomAbout(1.5, size.w / 2, size.h / 2)} aria-label="Zoom in">+</button>
        <button type="button" onClick={resetView}>FIT</button>
      </div>
    </div>
  );
}
