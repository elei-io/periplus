import "server-only"

// Transport only: the Python API validates public scope and stores collections.
export async function proxyCollection(request: Request, path: string) {
  const token = process.env.PERIPLUS_PUBLIC_API_TOKEN
  if (!token) return Response.json({ detail: "Collections are not configured yet." }, { status: 503 })
  try {
    const upstream = await fetch(new URL(path, process.env.PERIPLUS_API_URL ?? "http://127.0.0.1:8000"), {
      method: request.method,
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: request.method === "POST" ? request.body : undefined,
      ...(request.method === "POST" ? { duplex: "half" } : {}),
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(30_000)]),
      cache: "no-store",
    })
    return new Response(upstream.body, { status: upstream.status, headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store",
      ...(upstream.headers.has("retry-after") ? { "retry-after": upstream.headers.get("retry-after")! } : {}),
    } })
  } catch {
    return Response.json({ detail: "Collections are temporarily unavailable. Please try again." }, { status: 503 })
  }
}
