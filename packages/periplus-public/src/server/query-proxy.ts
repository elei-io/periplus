import "server-only"

// Transport only. Python owns query policy, preparation and execution.
export async function proxyQuery(request: Request, path: string) {
  const token = process.env.PERIPLUS_QUERY_API_TOKEN
  if (!token) return Response.json({ detail: "Periplus connection is not configured." }, { status: 503 })
  const headers = new Headers({ authorization: `Bearer ${token}` })
  for (const name of ["content-type", "range"]) {
    const value = request.headers.get(name)
    if (value) headers.set(name, value)
  }
  try {
    const upstream = await fetch(new URL(path, process.env.PERIPLUS_QUERY_URL ?? "http://127.0.0.1:8010"), {
      method: request.method,
      headers,
      body: request.method === "POST" ? request.body : undefined,
      ...(request.method === "POST" ? { duplex: "half" } : {}),
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(30_000)]),
      cache: "no-store",
    })
    const responseHeaders = new Headers()
    for (const name of ["content-type", "content-length", "content-range", "accept-ranges", "cache-control", "retry-after"]) {
      const value = upstream.headers.get(name)
      if (value) responseHeaders.set(name, value)
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders })
  } catch {
    return Response.json({ detail: "Periplus is temporarily unavailable." }, { status: 503 })
  }
}
