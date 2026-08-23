// Thin fetch client — the only module allowed to know the API base URL /
// axios config. Hooks call this; components never call it directly.
import axios from "axios";

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
});
