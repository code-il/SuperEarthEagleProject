/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The FastAPI backend (uvicorn backend.api.main:app --port 8000) is proxied at /api,
// so the browser talks to one origin and no CORS setup is needed in development.
const backend = process.env.SPHEREX_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: { "/api": { target: backend, changeOrigin: true, timeout: 600_000, proxyTimeout: 600_000 } } },
  preview: { port: 4173, proxy: { "/api": { target: backend, changeOrigin: true } } },
  build: { chunkSizeWarningLimit: 6000 },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"], setupFiles: ["src/test/setup.ts"] },
});
