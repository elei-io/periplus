import { strict as assert } from "node:assert"
import { test } from "node:test"
import { apiErrorFromResponse } from "./api.ts"

test("gateway HTML becomes a readable error without markup", async () => {
  const error = await apiErrorFromResponse(new Response("<!DOCTYPE html><title>Cloudflare</title>", { status: 502 }))
  assert.equal(error.status, 502)
  assert.match(error.message, /temporarily unavailable.*502/)
  assert.equal(error.detail, null)
})

test("structured API failures preserve their explanation", async () => {
  const error = await apiErrorFromResponse(new Response(JSON.stringify({ detail: "Build is not ready" }), { status: 409 }))
  assert.equal(error.message, "Build is not ready")
})
