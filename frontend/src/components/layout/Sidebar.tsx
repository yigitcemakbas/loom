import { NavLink } from "react-router-dom";

export function Sidebar() {
  return (
    <aside className="sidebar">
      <nav>
        <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
          Overview
        </NavLink>
        <NavLink to="/signals" className={({ isActive }) => (isActive ? "active" : "")}>
          Signals
        </NavLink>
        <NavLink to="/risks" className={({ isActive }) => (isActive ? "active" : "")}>
          Risk Tracker
        </NavLink>
        <NavLink to="/filings" className={({ isActive }) => (isActive ? "active" : "")}>
          Filings
        </NavLink>
        <NavLink to="/system" className={({ isActive }) => (isActive ? "active" : "")}>
          System
        </NavLink>
      </nav>
    </aside>
  );
}
