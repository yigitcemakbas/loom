import { Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { SignIn } from "./auth/SignIn";
import { Sidebar } from "./components/layout/Sidebar";
import { StaleBuildNotice } from "./components/layout/StaleBuildNotice";
import { TopBar } from "./components/layout/TopBar";
import { CompanyDetailPage } from "./pages/CompanyDetailPage";
import { TodayPage } from "./pages/TodayPage";
import { TerminalPage } from "./pages/TerminalPage";
import { FilingsPage } from "./pages/FilingsPage";
import { ChangesPage } from "./pages/ChangesPage";
import { PortfolioPage } from "./pages/PortfolioPage";
import { FundamentalsPage } from "./pages/FundamentalsPage";
import { RiskTrackerPage } from "./pages/RiskTrackerPage";
import { SignalFeedPage } from "./pages/SignalFeedPage";
import { SystemStatusPage } from "./pages/SystemStatusPage";

function App() {
  const { user, loading } = useAuth();

  // Held until the stored token has been checked. Rendering the sign-in screen
  // during this window makes a returning user see a login form for a moment on
  // every load, which reads as having been signed out.
  if (loading) {
    return <div className="signin-shell"><div className="signin-brand">LOOM</div></div>;
  }
  if (!user) return <SignIn />;

  return (
    <div className="app-shell">
      <div className="brand">LOOM</div>
      <TopBar />
      <Sidebar />
      <main className="main-content">
        <StaleBuildNotice />
        <Routes>
          <Route path="/" element={<TodayPage />} />
          <Route path="/terminal" element={<TerminalPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/changed" element={<ChangesPage />} />
          <Route path="/numbers" element={<FundamentalsPage />} />
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
