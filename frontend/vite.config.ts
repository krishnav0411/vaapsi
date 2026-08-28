import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy lets the React app (Vite :5173) hit the FastAPI JSON API
// (:8000) same-origin during D7.3+ render probes — no CORS anywhere.
// base /app/: the D7.5 cutover serves the build from FastAPI at /app,
// so every emitted asset URL must be /app/assets/*-relative.
export default defineConfig({
  base: "/app/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/dashboard": "http://localhost:8000",
    },
  },
});
