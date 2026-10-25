import { Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { AlertsPage } from "./routes/AlertsPage";
import { FleetOverviewPage } from "./routes/FleetOverviewPage";
import { HostPage } from "./routes/HostPage";
import { RulesPage } from "./routes/RulesPage";

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<FleetOverviewPage />} />
        <Route path="/hosts/:hostname" element={<HostPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/rules" element={<RulesPage />} />
      </Routes>
    </AppShell>
  );
}
