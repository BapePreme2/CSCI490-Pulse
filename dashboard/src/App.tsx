import { Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { FleetOverviewPage } from "./routes/FleetOverviewPage";
import { HostPage } from "./routes/HostPage";

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<FleetOverviewPage />} />
        <Route path="/hosts/:hostname" element={<HostPage />} />
      </Routes>
    </AppShell>
  );
}
