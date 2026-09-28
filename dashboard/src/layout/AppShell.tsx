import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/" className="app-title">
          Pulse
        </Link>
      </header>
      <main className="app-content">{children}</main>
    </div>
  );
}
