import { Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/layout/Sidebar";
import { CompanyDetailPage } from "./pages/CompanyDetailPage";
import { WatchlistPage } from "./pages/WatchlistPage";

function App() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-content">
        <Routes>
          <Route path="/" element={<WatchlistPage />} />
          <Route path="/companies/:ticker" element={<CompanyDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
