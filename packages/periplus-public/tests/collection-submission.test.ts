import assert from "node:assert/strict"
import { test } from "node:test"
import { publicCollectionSpec } from "../src/lib/collection-submission.ts"
const base = { kind: "url" as const, input: "https://example.com/a?x=1", depth: 1, scope: "internal" as const, maxPages: 25, maxLinks: 5000, sections: "" }
test("public intent maps link scope to navigation SQL and preserves URL identity", () => {
  const spec = publicCollectionSpec(base)
  assert.deepEqual(spec.seed_urls, [base.input])
  assert.equal(spec.follow_sql, "SELECT target_url AS url FROM nav.links WHERE relation_scope IN ('self', 'same_origin', 'same_host', 'same_site')")
  assert.equal(spec.follow_link_limit, 5000)
  assert.throws(() => publicCollectionSpec({...base, maxLinks: 10001}), /link limit/)
  assert.equal(spec.request_class, "public")
  assert.equal(publicCollectionSpec({...base, scope: "external"}).follow_sql, "SELECT target_url AS url FROM nav.links WHERE relation_scope = 'external'")
  assert.equal(publicCollectionSpec({...base, scope: "both"}).follow_sql, "SELECT target_url AS url FROM nav.links")
})
test("description, zero depth, and section limits use the collection contract", () => {
  const spec = publicCollectionSpec({...base, kind: "description", input: "  Robotics sources  ", depth: 0, sections: "https://example.com/docs\n"})
  assert.deepEqual(spec.seed_urls, [])
  assert.equal(spec.seed_description, "Robotics sources")
  assert.equal(spec.max_depth, 0)
  assert.deepEqual(spec.allowed_sections, ["https://example.com/docs"])
  assert.throws(() => publicCollectionSpec({...base, depth: 101}), /bounds/)
  assert.throws(() => publicCollectionSpec({...base, maxPages: 100001}), /bounds/)
  assert.throws(() => publicCollectionSpec({...base, sections: Array(11).fill("https://example.com/").join("\n")}), /10/)
})

test("retention defaults to forever and finite intent is validated", () => {
  assert.equal(publicCollectionSpec(base).retention_seconds, null)
  assert.equal(publicCollectionSpec({...base, retentionSeconds: 604800}).retention_seconds, 604800)
  for (const retentionSeconds of [0, -1, 1.5, 315360001]) {
    assert.throws(() => publicCollectionSpec({...base, retentionSeconds}), /retention/)
  }
})

test("submission accepts expanded policy choices within backend bounds", () => {
  const spec = publicCollectionSpec({...base, depth: 5, maxPages: 5000})
  assert.equal(spec.max_depth, 5)
  assert.equal(spec.page_limit, 5000)
})
