import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import ts from "typescript"

// Exercise the server-only transport with controlled admission and upstream HTTP.
function loadProxy(admit: (...args: unknown[]) => Promise<Response | null>) {
  const source = readFileSync(new URL("../src/server/query-proxy.ts", import.meta.url), "utf8")
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  const exports: { proxyQuery?: (request: Request, path: string, mode?: "stable" | "experimental") => Promise<Response> } = {}
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


test("execution modes route independently and experimental never falls back", async () => {
  const oldFetch = globalThis.fetch
  const names = ["PERIPLUS_QUERY_API_TOKEN", "PERIPLUS_QUERY_URL", "PERIPLUS_QUERY_EXPERIMENTAL_URL"]
  const previous = names.map(name => process.env[name])
  process.env.PERIPLUS_QUERY_API_TOKEN = "internal-secret"
  process.env.PERIPLUS_QUERY_URL = "http://stable.internal"
  process.env.PERIPLUS_QUERY_EXPERIMENTAL_URL = "http://experimental.internal"
  try {
    const admissions: unknown[][] = []
    const proxy = loadProxy(async (...args) => { admissions.push(args); return null })
    const calls: string[] = []
    globalThis.fetch = async (url) => { calls.push(String(url)); return Response.json({ ok: true }) }
    for (const mode of ["stable", "experimental"] as const) {
      for (const operation of ["exec", "prep", "helpers"]) {
        const method = operation === "helpers" ? "GET" : "POST"
        const response = await proxy(new Request("https://public.example", { method }), `/query/${operation}`, mode)
        assert.equal(response.status, 200)
        await response.json()
        assert.equal(calls.at(-1), `http://${mode}.internal/query/${operation}`)
      }
    }
    assert.deepEqual(admissions.map(args => args[2]), [true, false, true, false])
    calls.length = 0
    delete process.env.PERIPLUS_QUERY_EXPERIMENTAL_URL
    assert.equal((await proxy(new Request("https://public.example", { method: "POST" }), "/query/exec", "experimental")).status, 503)
    assert.equal(calls.length, 0)
    process.env.PERIPLUS_QUERY_EXPERIMENTAL_URL = "http://experimental.internal"
    globalThis.fetch = async url => { calls.push(String(url)); throw new Error("unavailable") }
    assert.equal((await proxy(new Request("https://public.example", { method: "POST" }), "/query/exec", "experimental")).status, 503)
    assert.deepEqual(calls, ["http://experimental.internal/query/exec"])
  } finally {
    globalThis.fetch = oldFetch
    names.forEach((name, index) => { if (previous[index] === undefined) delete process.env[name]; else process.env[name] = previous[index] })
  }
})

test("query stream negotiation and cancellation reach the upstream without buffering", async () => {
  const oldFetch = globalThis.fetch
  const oldToken = process.env.PERIPLUS_QUERY_API_TOKEN
  process.env.PERIPLUS_QUERY_API_TOKEN = "internal-secret"
  let cancelled = false
  try {
    globalThis.fetch = async (_url, options) => {
      assert.equal(new Headers(options?.headers).get("accept"), "application/x-ndjson")
      return new Response(new ReadableStream({
        start(controller) { controller.enqueue(new TextEncoder().encode('{"type":"metadata"}\n')) },
        cancel() { cancelled = true },
      }), { headers: { "content-type": "application/x-ndjson", "x-accel-buffering": "no" } })
    }
    const response = await loadProxy(async () => null)(new Request("https://public.example/api/query/exec", {
      method: "POST", headers: { accept: "application/x-ndjson" }, body: '{"sql":"SELECT 1"}',
    }), "/query/exec")
    assert.equal(response.headers.get("content-type"), "application/x-ndjson")
    assert.equal(response.headers.get("x-accel-buffering"), "no")
    const reader = response.body!.getReader()
    assert.equal(new TextDecoder().decode((await reader.read()).value), '{"type":"metadata"}\n')
    await reader.cancel()
    assert.equal(cancelled, true)
  } finally {
    globalThis.fetch = oldFetch
    if (oldToken === undefined) delete process.env.PERIPLUS_QUERY_API_TOKEN
    else process.env.PERIPLUS_QUERY_API_TOKEN = oldToken
  }
})
