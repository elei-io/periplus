import { createHmac, timingSafeEqual } from "node:crypto"

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const RECEIPT_SECONDS = 7 * 24 * 60 * 60

function secret() {
  const value = process.env.PERIPLUS_PUBLIC_RECEIPT_SECRET
  if (!value || value.length < 32) throw new Error("Crawl receipts are not configured.")
  return value
}

export function requireReceiptConfiguration() {
  secret()
}

export function createReceipt(id: string, now = Date.now()): string {
  if (!UUID.test(id)) throw new Error("Invalid crawl identity.")
  const payload = `${id}.${Math.floor(now / 1000) + RECEIPT_SECONDS}`
  return `${payload}.${createHmac("sha256", secret()).update(payload).digest("base64url")}`
}

export function readReceipt(receipt: string, now = Date.now()): string | null {
  const [id, expiry, signature, extra] = receipt.split(".")
  if (extra !== undefined || !UUID.test(id ?? "") || !/^\d+$/.test(expiry ?? "")) return null
  if (Number(expiry) <= Math.floor(now / 1000) || !/^[\w-]{43}$/.test(signature ?? "")) return null
  const expected = createHmac("sha256", secret()).update(`${id}.${expiry}`).digest("base64url")
  return timingSafeEqual(Buffer.from(signature), Buffer.from(expected)) ? id : null
}
