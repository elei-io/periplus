// Raw evidence is served by the repository-owning API, never the SQL service.
export async function GET(request: Request, context: RouteContext<"/api/content/[id]">) {
  const { id } = await context.params
  if (!/^[0-9a-f]{64}$/.test(id)) return Response.json({ detail: "Invalid content identity." }, { status: 400 })
  const token = process.env.PERIPLUS_PUBLIC_API_TOKEN
  if (!token) return Response.json({ detail: "Content retrieval is not configured." }, { status: 503 })
  try {
    const upstream = await fetch(new URL(`/documents/by-content/${id}/content`, process.env.PERIPLUS_API_URL ?? "http://127.0.0.1:8000"), {
      headers: { authorization: `Bearer ${token}` },
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(30_000)]),
      cache: "no-store",
    })
    const headers = new Headers({ "cache-control": "no-store" })
    for (const name of ["content-type", "content-length", "content-disposition", "content-security-policy", "x-content-type-options", "etag"]) {
      const value = upstream.headers.get(name)
      if (value) headers.set(name, value)
    }
    return new Response(upstream.body, { status: upstream.status, headers })
  } catch {
    return Response.json({ detail: "Content is temporarily unavailable." }, { status: 503 })
  }
}
