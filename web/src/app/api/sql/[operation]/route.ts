const supportedOperations = new Set(["metadata", "query"])

function apiBaseUrl() {
  return process.env.PERIPLUS_API_URL ?? "http://127.0.0.1:8000"
}

async function proxySqlRequest(
  request: Request,
  context: { params: Promise<{ operation: string }> }
) {
  const { operation } = await context.params
  if (!supportedOperations.has(operation)) {
    return Response.json({ detail: "SQL operation not found." }, { status: 404 })
  }

  if (
    (request.method === "GET" && operation !== "metadata") ||
    (request.method === "POST" && operation !== "query")
  ) {
    return Response.json({ detail: "Method not allowed." }, { status: 405 })
  }

  const target = new URL(`/sql/${operation}`, apiBaseUrl())
  const headers = new Headers()
  const contentType = request.headers.get("content-type")
  if (contentType) headers.set("content-type", contentType)
  headers.set("accept", "application/json")

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      signal: request.signal,
    })
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    })
  } catch {
    return Response.json(
      { detail: "Periplus API is unavailable." },
      { status: 503 }
    )
  }
}

export function GET(
  request: Request,
  context: { params: Promise<{ operation: string }> }
) {
  return proxySqlRequest(request, context)
}

export function POST(
  request: Request,
  context: { params: Promise<{ operation: string }> }
) {
  return proxySqlRequest(request, context)
}
