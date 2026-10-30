import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The PIE backend on the server (217.217.249.121:8080). In development the pages call
// /usage/... and /logs/... on this server, which forwards them to the backend, so the
// browser never makes a cross-site request. For a local backend (start_backend.ps1, port 8000):
//   $env:PIE_BACKEND = "http://localhost:8000"; npm run dev
const backend = process.env.PIE_BACKEND || "http://217.217.249.121:8080";
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
