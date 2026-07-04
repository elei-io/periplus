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
      "/extract": apiProxyTarget,
      "/index": apiProxyTarget,
      "/scrape": apiProxyTarget,
      "/search": apiProxyTarget,
      "/tasks": apiProxyTarget,
    },
  },
})
