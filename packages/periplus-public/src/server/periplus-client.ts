import "server-only"

export class PeriplusError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
    this.name = "PeriplusError"
  }
}

export async function periplusRequest(path: string, init: RequestInit = {}): Promise<unknown> {
  const token = process.env.PERIPLUS_PUBLIC_API_TOKEN
  if (!token) throw new PeriplusError("Periplus connection is not configured.", 503)
  const signal = init.signal
    ? AbortSignal.any([init.signal, AbortSignal.timeout(30_000)])
    : AbortSignal.timeout(30_000)
  const response = await fetch(new URL(path, process.env.PERIPLUS_API_URL ?? "http://127.0.0.1:8000"), {
    ...init,
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    signal,
    cache: "no-store",
  })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null
    throw new PeriplusError(typeof detail === "string" ? detail : "Periplus request failed.", response.status)
  }
  return body
}

export function periplusErrorResponse(error: unknown) {
  if (error instanceof PeriplusError) return Response.json({ detail: error.message }, { status: error.status })
  return Response.json({ detail: "Periplus is temporarily unavailable." }, { status: 503 })
}
