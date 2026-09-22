import { useEffect, useMemo, useRef, useState } from "react";
import type { ExposureGraph, ExposureNode } from "../../types/models";

interface Props {
  graph: ExposureGraph;
  stanceByTicker: Map<string, string>;
  selected: string | null;
  onSelect: (ticker: string | null) => void;
  width?: number;
  height?: number;
}

interface Placed extends ExposureNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

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

/** The dependency graph, drawn.
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
 *  Selecting a node dims everything it does not touch. That is the actual
 *  analytical act this view supports: not "rank these companies" but "if this
 *  one moves, what else does". */
export function PropagationMap({
  graph, stanceByTicker, selected, onSelect, width = 900, height = 560,
}: Props) {
  const [nodes, setNodes] = useState<Placed[]>([]);
  const frame = useRef<number>(0);

  const edges = graph.edges;

  useEffect(() => {
    if (graph.nodes.length === 0) return;

    const placed: Placed[] = graph.nodes.map((n, i) => ({
      ...n,
      ...seeded(i, graph.nodes.length, width, height),
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
        n.vx += (width / 2 - n.x) * GRAVITY;
        n.vy += (height / 2 - n.y) * GRAVITY;

        n.vx *= DAMPING;
        n.vy *= DAMPING;
        n.x = Math.max(24, Math.min(width - 24, n.x + n.vx));
        n.y = Math.max(24, Math.min(height - 24, n.y + n.vy));
      }
    }

    setNodes(placed);
    return () => cancelAnimationFrame(frame.current);
  }, [graph, width, height, edges]);

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

  if (nodes.length === 0) {
    return <div className="empty-state" style={{ height }}>Laying out the graph…</div>;
  }

  return (
    <svg
      className="propagation-map"
      viewBox={`0 0 ${width} ${height}`}
      onClick={() => onSelect(null)}
    >
      {edges.map((e, i) => {
        const a = byTicker.get(e.hub);
        const b = byTicker.get(e.dependent);
        if (!a || !b) return null;
        const lit = !connected || (connected.has(e.hub) && connected.has(e.dependent));
        return (
          <line
            key={i}
            x1={a.x} y1={a.y} x2={b.x} y2={b.y}
            stroke={lit && selected ? "var(--accent)" : "var(--border-strong)"}
            strokeWidth={lit && selected ? 1.1 : 0.5}
            opacity={connected ? (lit ? 0.85 : 0.07) : 0.34}
          />
        );
      })}

      {nodes.map((n) => {
        const lit = !connected || connected.has(n.ticker);
        // Size carries reach: a company many others depend on is drawn larger,
        // because that is exactly what makes its events worth watching.
        const r = 3.4 + Math.sqrt(n.reach) * 2.6;
        const isSelected = n.ticker === selected;
        return (
          <g
            key={n.ticker}
            opacity={lit ? 1 : 0.12}
            onClick={(event) => {
              event.stopPropagation();
              onSelect(isSelected ? null : n.ticker);
            }}
            style={{ cursor: "pointer" }}
          >
            <circle
              cx={n.x} cy={n.y} r={r}
              fill={stanceColour(stanceByTicker.get(n.ticker))}
              stroke={isSelected ? "var(--accent)" : "var(--bg-panel)"}
              strokeWidth={isSelected ? 2 : 1}
            />
            {(n.reach >= 3 || isSelected) && (
              <text
                x={n.x} y={n.y - r - 3}
                textAnchor="middle"
                className="map-label"
                fill={isSelected ? "var(--accent)" : "var(--text-dim)"}
              >
                {n.ticker}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
