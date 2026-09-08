import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, path.resolve(import.meta.dirname, "../.."), ""), ...process.env }
  const token = env.PERIPLUS_ADMIN_API_TOKEN
  return {
    plugins: [react(), tailwindcss(), {
      name: "admin-origin-check",
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          const origin = request.headers.origin
          if (origin && new URL(origin).host !== request.headers.host) {
            response.statusCode = 403
            response.end("Cross-origin administrative requests are not allowed.")
            return
          }
          next()
        })
      },
    }],
    resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": {
          target: env.PERIPLUS_API_URL ?? "http://127.0.0.1:8000",
          changeOrigin: true,
          rewrite: (url) => url.replace(/^\/api(?=\/|$)/, ""),
          configure(proxy) {
            proxy.on("proxyReq", (request) => request.setHeader("authorization", `Bearer ${token}`))
          },
        },
      },
    },
  }
})
