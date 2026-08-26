// Thin fetch client, the only module allowed to know the API base URL /
// axios config. Hooks call this; components never call it directly.
import axios from "axios";

// Default to the same-origin "/api" path, which Vite proxies to the backend
// (see vite.config.ts). Same-origin means no CORS involved and no dependency
// on which port Vite bound to. VITE_API_BASE_URL can still override this to
// point at a backend directly.
export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
});
