import assert from "node:assert/strict"
import test from "node:test"
import { createReceipt, readReceipt } from "../src/server/crawl-receipts.ts"
import { admitSubmission, submissionUrl, isSameOrigin } from "../src/server/crawl-submission.ts"

process.env.PERIPLUS_PUBLIC_RECEIPT_SECRET = "test-secret-with-at-least-thirty-two-characters"
const id = "0d0beac7-504c-4a4b-bd20-195f9df0bf76"

test("receipts allow only their signed request within their lifetime", () => {
  const receipt = createReceipt(id, 1_000_000)
  assert.equal(readReceipt(receipt, 1_000_000), id)
  assert.equal(readReceipt(receipt.replace(id, "1" + id.slice(1)), 1_000_000), null)
  assert.equal(readReceipt(receipt + ".extra", 1_000_000), null)
  assert.equal(readReceipt(receipt, 1_000_000 + 7 * 86400 * 1000), null)
  assert.equal(readReceipt(id, 1_000_000), null)
})

test("submission accepts one URL and no operator controls", () => {
  assert.equal(submissionUrl({ url: "https://example.com/#fragment" }), "https://example.com/")
  for (const value of [{ url: "file:///etc/passwd" }, { url: "https://user:pass@example.com" }, { url: "https://example.com", depth: 4 }, { urls: ["https://example.com"] }, null]) {
    assert.equal(submissionUrl(value), null)
  }
})

test("anonymous submissions have a bounded per-process admission rate", () => {
  for (let i = 0; i < 10; i++) assert.equal(admitSubmission(100_000), true)
  assert.equal(admitSubmission(100_000), false)
  assert.equal(admitSubmission(160_000), true)
})

test("submission checks the browser authority behind a reverse proxy", () => {
  const request = (origin: string) => new Request("http://container:3000/api/crawls", {
    headers: { host: "catalogue.example", origin },
  })
  assert.equal(isSameOrigin(request("https://catalogue.example")), true)
  assert.equal(isSameOrigin(request("https://attacker.example")), false)
  assert.equal(isSameOrigin(request("null")), false)
  assert.equal(isSameOrigin(request("")), false)
})
