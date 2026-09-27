import { useState } from "react";
import { useContradictions } from "../../hooks/useContradictions";

/** Where Loom's own sources disagree.
 *
 *  Everything else on this page is an average. The brief averages finding
 *  directions, the factor panel averages percentiles into themes, and both
 *  reduce a company to a number. This panel is the one output that refuses to
 *  combine, because the moment worth acting on is precisely the one where the
 *  evidence does not agree: confident language over deteriorating cash is not
 *  a neutral reading, it is a thesis, and averaging it produces zero.
 *
 *  Placed above the verdict on the company page for the same reason. A
 *  contradiction is not a component of a conclusion, it is a reason to
 *  distrust one, and a reader who sees the conclusion first has already
 *  formed a view by the time they reach the objection. */
function TechnicalNote({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="contradiction-more" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "less" : "the short version"}
      </button>
      {open && <p className="contradiction-technical">{text}</p>}
    </>
  );
}

export function ContradictionPanel({ ticker }: { ticker: string }) {
  const { data, isLoading } = useContradictions(ticker);

  if (isLoading || !data) return null;

  // An empty result is a real answer and says so, rather than rendering
  // nothing and leaving a reader unsure whether Loom looked.
  if (data.contradictions.length === 0) {
    if (data.compared_sources.length < 2) return null;
    return (
      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Does the evidence agree?</span>
        </div>
        <p className="contradiction-agree">
          Loom compared {data.compared_sources.join(", ")} and found no
          disagreement worth flagging. That is a finding, not an absence: these
          sources are independent, and when they point the same way the verdict
          rests on more than one kind of evidence.
        </p>
      </div>
    );
  }

  return (
    <div className="panel panel-contradiction">
      <div className="panel-head">
        <span className="panel-title">The evidence disagrees</span>
        <span className="faint" style={{ fontSize: 9 }}>
          {data.contradictions.length}
        </span>
      </div>

      {data.contradictions.map((c) => (
        <article key={c.key} className="contradiction">
          <h4>{c.headline}</h4>
          <div className="contradiction-sides">
            <p className="side-better">
              <span className="side-mark">▲</span> {c.says_better}
            </p>
            <p className="side-worse">
              <span className="side-mark">▼</span> {c.says_worse}
            </p>
          </div>
          {/* The part that makes it a thesis rather than an observation.
              Leads with the version written for a reader who does not already
              know the vocabulary, because in testing that was the difference
              between the most-cited output Loom has and one a reader gave up
              on. The expert wording is still here, one click away, for whoever
              wants the shorter form. */}
          <p className="contradiction-why">{c.plain || c.why_it_matters}</p>
          {c.settled_by && (
            <p className="contradiction-settles">
              <strong>What would settle it:</strong> {c.settled_by}
            </p>
          )}
          {c.plain && c.why_it_matters !== c.plain && <TechnicalNote text={c.why_it_matters} />}
        </article>
      ))}

      <p className="faint contradiction-note">
        Loom is not taking a side here. These are two of its own sources pointing
        opposite ways, which is worth more of your attention than either one alone.
      </p>
    </div>
  );
}
