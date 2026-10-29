import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The PIE backend (start_backend.ps1) runs on port 8000. In development the pages call
// /usage/... and /logs/... on this server, which forwards them to the backend, so the
// browser never makes a cross-site request. Change PIE_BACKEND to use another backend.
const backend = process.env.PIE_BACKEND || "http://localhost:8000";
const proxy = {
  "/usage": { target: backend, changeOrigin: true },
  "/logs/api": { target: backend, changeOrigin: true },
  "/health": { target: backend, changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  // Relative asset paths: the built app works from any folder or web server.
  base: "./",
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
});
