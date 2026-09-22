import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.tsx";
import { AuthProvider } from "./auth/AuthContext";
import "./styles/theme.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // This app only ever talks to a local backend. TanStack Query's
      // browser online/offline detection is meant for real network loss and
      // has no useful signal for "is localhost:8000 up", left at its
      // default, a failed local request can get stuck in a "paused" retry
      // state instead of surfacing an error. `always` makes retries purely
      // timer-based, which is what we actually want here.
      networkMode: "always",
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* Inside the query client, because signing out clears the cache:
            every response was fetched as somebody, and leaving them would show
            one account's portfolio to the next person on this browser. */}
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
