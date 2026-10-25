import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/" className="app-title">
          Pulse
        </Link>
        <nav className="app-nav" aria-label="Main">
          <NavLink to="/" end className="app-nav-link">
            Fleet
          </NavLink>
          <NavLink to="/alerts" className="app-nav-link">
            Alerts
          </NavLink>
          <NavLink to="/rules" className="app-nav-link">
            Alert rules
          </NavLink>
        </nav>
      </header>
      <main className="app-content">{children}</main>
    </div>
  );
}
