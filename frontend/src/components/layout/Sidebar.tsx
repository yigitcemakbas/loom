import { NavLink } from "react-router-dom";

/** Two front doors, then the machinery.
 *
 *  The split is the navigation's main job. Loom serves two readers who want
 *  opposite things from the same data: one needs a single company explained,
 *  the other needs the whole universe ranked. A single dashboard trying to do
 *  both was serving neither, which is why the beginner and professional views
 *  are separate destinations rather than a density toggle. */
export function Sidebar() {
  const cls = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : "");
  return (
    <aside className="sidebar">
      <nav>
        <div className="sidebar-section">Start here</div>
        <NavLink to="/" end className={cls}>Today</NavLink>
        <NavLink to="/terminal" className={cls}>Terminal</NavLink>
        <NavLink to="/numbers" className={cls}>The numbers</NavLink>

        <div className="sidebar-section">Detail</div>
        <NavLink to="/signals" className={cls}>Findings</NavLink>
        <NavLink to="/risks" className={cls}>Risks</NavLink>
        <NavLink to="/filings" className={cls}>Filings</NavLink>
        <NavLink to="/system" className={cls}>System</NavLink>
      </nav>
    </aside>
  );
}
