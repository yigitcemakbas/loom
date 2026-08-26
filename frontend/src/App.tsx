import { Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/layout/Sidebar";
import { TopBar } from "./components/layout/TopBar";
import { CompanyDetailPage } from "./pages/CompanyDetailPage";
import { DashboardPage } from "./pages/DashboardPage";
import { FilingsPage } from "./pages/FilingsPage";
import { RiskTrackerPage } from "./pages/RiskTrackerPage";
import { SignalFeedPage } from "./pages/SignalFeedPage";
import { SystemStatusPage } from "./pages/SystemStatusPage";

function App() {
  return (
    <div className="app-shell">
      <div className="brand">LOOM</div>
      <TopBar />
      <Sidebar />
      <main className="main-content">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/signals" element={<SignalFeedPage />} />
          <Route path="/risks" element={<RiskTrackerPage />} />
          <Route path="/filings" element={<FilingsPage />} />
          <Route path="/system" element={<SystemStatusPage />} />
          <Route path="/companies/:ticker" element={<CompanyDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
