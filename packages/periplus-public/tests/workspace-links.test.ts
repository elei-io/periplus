import assert from "node:assert/strict"
import test from "node:test"
import { buildLink, discoverLink, sqlBuildLink } from "../src/lib/workspace-links.ts"

test("SQL handoff preserves executed SQL, parameters and ordered types without inferring requirements", () => {
  const result = { sql: "SELECT ? AS name, 2::BIGINT AS count", parameters: ["a & b # c"], columns: ["name", "count"], types: ["VARCHAR", "BIGINT"] }
  const url = new URL(sqlBuildLink(result), "https://example.org")
  const draft = JSON.parse(url.searchParams.get("draft")!)
  assert.equal(url.pathname, "/build")
  assert.equal(url.searchParams.has("run"), false)
  assert.ok(url.searchParams.get("question")!.includes(result.sql))
  assert.ok(url.searchParams.get("question")!.includes(JSON.stringify(result.parameters)))
  assert.deepEqual(draft.fields.map((field: { name: string; type: string; nullable: boolean }) => [field.name, field.type, field.nullable]), [["name", "VARCHAR", true], ["count", "BIGINT", true]])
  assert.equal(draft.grain, "")
  assert.equal(draft.population, "")
})

test("contextual routes preserve special characters and do not execute on arrival", () => {
  const question = "Explore https://example.org/?a=1&b=2 # café"
  for (const link of [discoverLink(question), buildLink(question)]) {
    const params = new URL(link, "https://example.org").searchParams
    assert.equal(params.get("question"), question)
    assert.equal(params.has("run"), false)
  }
})
