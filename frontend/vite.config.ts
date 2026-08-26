import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // The frontend calls same-origin "/api/*" and Vite forwards to the
      // backend. This deliberately avoids pointing the browser directly at
      // http://localhost:8000, which coupled the app to one exact frontend
      // port (any other port was rejected by the backend's CORS rule) and to
      // one exact host spelling. Proxying keeps every request same-origin, so
      // it works on whatever port Vite happens to bind.
      //
      // Target is 127.0.0.1 rather than localhost on purpose: uvicorn binds
      // IPv4 only, while "localhost" can resolve to IPv6 ::1 first.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
