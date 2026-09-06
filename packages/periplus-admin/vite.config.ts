import path from "node:path"
import { timingSafeEqual } from "node:crypto"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, path.resolve(import.meta.dirname, "../.."), ""), ...process.env }
  const token = env.PERIPLUS_ADMIN_API_TOKEN
  return {
    plugins: [react(), tailwindcss(), {
      name: "admin-access",
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          const expected = Buffer.from(`Basic ${Buffer.from(`admin:${token}`).toString("base64")}`)
          const actual = Buffer.from(request.headers.authorization ?? "")
          if (!token || actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
            response.statusCode = token ? 401 : 503
            response.setHeader("WWW-Authenticate", 'Basic realm="Periplus Admin"')
            response.end("Administrative credentials required.")
            return
          }
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
