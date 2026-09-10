import assert from "node:assert/strict"
import test from "node:test"
import { hasWorkspaceInput, indexablePaths, pageMetadata, parsePublicOrigin, publicOrigin } from "../src/lib/seo.ts"

test("public origin cannot contain paths, credentials or URL payloads", () => {
  assert.equal(parsePublicOrigin(undefined), undefined)
  assert.equal(parsePublicOrigin(""), undefined)
  assert.equal(parsePublicOrigin("https://catalogue.example/"), "https://catalogue.example")
  for (const value of ["http://catalogue.example", "https://user:pass@catalogue.example", "https://catalogue.example/path", "https://catalogue.example/?sql=secret", "https://catalogue.example/#fragment", "not-a-url"]) {
    assert.throws(() => parsePublicOrigin(value))
  }
})

test("workspace inputs, including empty or repeated parameters, are not indexable", () => {
  assert.equal(hasWorkspaceInput({}, ["sql", "parameters", "run"]), false)
  assert.equal(hasWorkspaceInput({ utm_source: "docs" }, ["sql", "parameters", "run"]), false)
  assert.equal(hasWorkspaceInput({ sql: "" }, ["sql", "parameters", "run"]), true)
  assert.equal(hasWorkspaceInput({ sql: ["SELECT 1", "SELECT 2"] }, ["sql"]), true)
  assert.equal(hasWorkspaceInput({ question: "private input" }, ["question", "run"]), true)
  assert.equal(hasWorkspaceInput({ request: "request-id" }, ["request"]), true)
  assert.deepEqual(pageMetadata("/sql", "SQL", "SQL console", false).robots, { index: false, follow: true })
})

test("sitemap includes clean public routes only and unconfigured builds are noindex", () => {
  assert.deepEqual(indexablePaths, ["/", "/about", "/docs", "/discover", "/build", "/sql", "/coverage"])
  const metadata = pageMetadata("/docs", "Documentation", "Read the documentation")
  assert.deepEqual(metadata.robots, { index: Boolean(publicOrigin), follow: true })
  assert.equal(metadata.alternates?.canonical, publicOrigin ? `${publicOrigin}/docs` : undefined)
})
