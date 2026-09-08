import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import ts from "typescript"

// Exercise the server-only transport with controlled admission and upstream HTTP.
function loadProxy(admit: (...args: unknown[]) => Promise<Response | null>) {
  const source = readFileSync(new URL("../src/server/query-proxy.ts", import.meta.url), "utf8")
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  const exports: { proxyQuery?: (request: Request, path: string) => Promise<Response> } = {}
  const require = (name: string) => {
    if (name === "server-only") return {}
    if (name === "./public-access") return { admitPublic: admit }
    if (name === "./telemetry") return { beginOperation: () => () => {} }
    throw new Error(`Unexpected dependency: ${name}`)
  }
  new Function("require", "exports", compiled)(require, exports)
  return exports.proxyQuery!
}

test("SDK uses the same admission and only its analytics label crosses the gateway", async () => {
  const oldFetch = globalThis.fetch
  const oldToken = process.env.PERIPLUS_QUERY_API_TOKEN
  const oldUrl = process.env.PERIPLUS_QUERY_URL
  process.env.PERIPLUS_QUERY_API_TOKEN = "internal-secret"
  process.env.PERIPLUS_QUERY_URL = "http://query.internal"
  try {
    const admissions: unknown[][] = []
    const proxy = loadProxy(async (...args) => { admissions.push(args); return null })
    for (const [input, expected] of [["sdk", "sdk"], ["admin", "public_console"], ["internal", "public_console"], ["", "public_console"]]) {
      globalThis.fetch = async (url, options) => {
        assert.equal(String(url), "http://query.internal/query/exec")
        const headers = new Headers(options?.headers)
        assert.equal(headers.get("authorization"), "Bearer internal-secret")
        assert.equal(headers.get("x-periplus-query-source"), expected)
        return Response.json({ rows: [[1]], truncated: false })
      }
      const response = await proxy(new Request("https://public.example/api/query/exec", {
        method: "POST", headers: { "x-periplus-query-source": input, authorization: "Bearer attacker" },
        body: JSON.stringify({ sql: "SELECT 1", parameters: [] }),
      }), "/query/exec")
      assert.deepEqual(await response.json(), { rows: [[1]], truncated: false })
    }
    assert.equal(admissions.length, 4)
    assert.ok(admissions.every(args => args[0] === "sql" && args[2] === true))
    let called = false
    globalThis.fetch = async () => { called = true; throw new Error("must not reach query") }
    const denied = loadProxy(async () => Response.json({ code: "feature_disabled" }, { status: 403 }))
    const response = await denied(new Request("https://public.example/api/query/exec", {
      method: "POST", headers: { "x-periplus-query-source": "sdk" }, body: "{}",
    }), "/query/exec")
    assert.equal(response.status, 403)
    assert.equal(called, false)
    const prep = loadProxy(async (...args) => { assert.equal(args[2], false); return new Response(null, { status: 429 }) })
    assert.equal((await prep(new Request("https://public.example/api/query/prep", { method: "POST" }), "/query/prep")).status, 429)
  } finally {
    globalThis.fetch = oldFetch
    if (oldToken === undefined) delete process.env.PERIPLUS_QUERY_API_TOKEN
    else process.env.PERIPLUS_QUERY_API_TOKEN = oldToken
    if (oldUrl === undefined) delete process.env.PERIPLUS_QUERY_URL
    else process.env.PERIPLUS_QUERY_URL = oldUrl
  }
})
