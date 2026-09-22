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

// The session token, held in localStorage so a sign-in survives a reload and a
// closed laptop. Ninety days server side, which matches: this is a tool
// somebody opens most mornings, and a session that expires weekly turns the
// sign-in email into a weekly chore.
//
// localStorage rather than a cookie because the token is sent as a bearer
// header: a cookie would need CORS credentials and a same-site policy to
// match, for no gain on a locally served app.
const TOKEN_KEY = "loom.session";

export function storedToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private browsing and blocked site data both throw here. A session that
    // lasts only as long as the tab is better than a page that will not load.
    return null;
  }
}

export function storeToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* see storedToken */
  }
}

apiClient.interceptors.request.use((config) => {
  const token = storedToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// A 401 means the session is gone: expired, revoked, or from a database that
// has since been reset. Clearing it here rather than in every caller means a
// stale token cannot leave the app in a state where every request fails and
// nothing explains why.
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401 && storedToken()) {
      storeToken(null);
      onUnauthorized?.();
    }
    return Promise.reject(error);
  },
);
