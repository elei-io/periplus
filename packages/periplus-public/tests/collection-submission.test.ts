import assert from "node:assert/strict"
import { test } from "node:test"
import { publicCollectionSpec } from "../src/lib/collection-submission.ts"
const base = { kind: "url" as const, input: "https://example.com/a?x=1", depth: 1, scope: "internal" as const, maxPages: 25, sections: "" }
test("public intent maps link scope to navigation SQL and preserves URL identity", () => {
  const spec = publicCollectionSpec(base)
  assert.deepEqual(spec.seed_urls, [base.input])
  assert.equal(spec.follow_sql, "SELECT target_url AS url FROM nav.links WHERE relation_scope IN ('self', 'same_origin', 'same_host', 'same_site')")
  assert.equal(spec.visibility, "public")
  assert.equal(publicCollectionSpec({...base, scope: "external"}).follow_sql, "SELECT target_url AS url FROM nav.links WHERE relation_scope = 'external'")
  assert.equal(publicCollectionSpec({...base, scope: "both"}).follow_sql, "SELECT target_url AS url FROM nav.links")
})
test("description, zero depth, and section limits use the collection contract", () => {
  const spec = publicCollectionSpec({...base, kind: "description", input: "  Robotics sources  ", depth: 0, sections: "https://example.com/docs\n"})
  assert.deepEqual(spec.seed_urls, [])
  assert.equal(spec.seed_description, "Robotics sources")
  assert.equal(spec.max_depth, 0)
  assert.deepEqual(spec.allowed_sections, ["https://example.com/docs"])
  assert.throws(() => publicCollectionSpec({...base, depth: 3}), /depth/)
  assert.throws(() => publicCollectionSpec({...base, maxPages: 1001}), /pages/)
  assert.throws(() => publicCollectionSpec({...base, sections: Array(11).fill("https://example.com/").join("\n")}), /10/)
})
