import assert from "node:assert/strict"
import { test } from "node:test"
import { ApiError, responseJson } from "../src/lib/api.ts"

test("plain-text server failures surface an HTTP error instead of a JSON parser error", async () => {
  await assert.rejects(responseJson(new Response("Internal Server Error", { status: 500 })),
    error => error instanceof ApiError && error.status === 500 && error.message === "Request failed (HTTP 500). Please try again.")
})

test("structured API errors preserve their code and retry delay", async () => {
  await assert.rejects(responseJson(Response.json({ detail: { detail: "Try later", code: "busy" } },
    { status: 429, headers: { "retry-after": "30" } })),
    error => error instanceof ApiError && error.code === "busy" && error.retryAfterSeconds === 30 && error.message === "Try later")
})
