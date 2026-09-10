import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import ts from "typescript"

test("internal browser flag covers the first pageview before SDK loaded and persists to other tabs", () => {
  const source = readFileSync(new URL('../src/instrumentation-client.ts', import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText
  const previous = { ...process.env }
  process.env.NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN = 'test-token'
  process.env.NEXT_PUBLIC_POSTHOG_HOST = 'https://example.org'
  process.env.NEXT_PUBLIC_ANALYTICS_ENVIRONMENT = 'production'
  let superProperties = {}
  const events: Array<{ properties: Record<string, unknown> }> = []
  const client = {
    init(_token: string, options: { before_send: (event: { properties: Record<string, unknown> }) => { properties: Record<string, unknown> }; loaded: (client: unknown) => void }) {
      events.push(options.before_send({ properties: { ...superProperties } }))
      options.loaded(client)
    },
    register(properties: object) { superProperties = { ...superProperties, ...properties } },
    setPersonProperties() {},
  }
  const require = (name: string) => {
    if (name === 'posthog-js') return client
    if (name.includes('navigation-analytics')) return { navigationAnalytics: () => null }
    if (name.includes('analytics-privacy')) return { redactAnalyticsProperties: (v: unknown) => v, analyticsUrl: (v: string) => v }
    throw new Error(name)
  }
  try {
    const run = new Function('require', 'document', 'window', 'exports', compiled)
    run(require, { addEventListener() {} }, { location: { search: '?analytics_test=1' } }, {})
    run(require, { addEventListener() {} }, { location: { search: '' } }, {})
    assert.deepEqual(events.map(event => event.properties.$internal_or_test_user), [true, true])
    assert.deepEqual(events.map(event => event.properties.analytics_version), [2, 2])
  } finally { process.env = previous }
})
