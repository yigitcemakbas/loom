import { ChangeFeed } from "../components/changes/ChangeFeed";

/** What moved since you last looked.
 *
 *  Its own destination rather than a band across the top of Today. The feed
 *  is long by nature, and putting it above the verdict cards pushed the thing
 *  the page exists for below the fold: a reader arriving at Today wants the
 *  companies worth a look, and a reader who wants to know what is different
 *  is asking a different question and will come here to ask it.
 */
export function ChangesPage() {
  return (
    <div>
      <header className="today-head">
        <h1>What changed</h1>
        <p className="today-sub">
          Loom re-reads its own verdicts, rescores the universe against filed
          financials, and matches every new filing against what it was already
          watching for. This is everything that crossed a line worth your
          attention, loudest first.
        </p>
      </header>
      <ChangeFeed />
    </div>
  );
}
