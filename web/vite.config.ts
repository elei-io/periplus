import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const apiProxyTarget =
  process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/calibrate": apiProxyTarget,
      "/catalogue": apiProxyTarget,
      "/extract": apiProxyTarget,
      "/index": apiProxyTarget,
      "/crawl": apiProxyTarget,
      "/crawls": apiProxyTarget,
      "/data-schemas": apiProxyTarget,
      "/query": apiProxyTarget,
      "/query-schemas": apiProxyTarget,
      "/search": apiProxyTarget,
      "/schema": apiProxyTarget,
      "/tasks": apiProxyTarget,
      "/task-runs": apiProxyTarget,
      "/operations": apiProxyTarget,
      "/artifacts": apiProxyTarget,
      "/urls": apiProxyTarget,
    },
  },
})
