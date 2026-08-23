import { NavLink } from "react-router-dom";

export function Sidebar() {
  return (
    <aside className="sidebar">
      <h1>LOOM</h1>
      <nav>
        <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
          Watchlist
        </NavLink>
        {/* SignalFeedPage lands in Phase 2 once the engine exists */}
      </nav>
    </aside>
  );
}
