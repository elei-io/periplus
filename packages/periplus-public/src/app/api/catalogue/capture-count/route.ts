// A fixed browsing read: no caller SQL or parameters cross this endpoint.
export async function GET(request: Request) {
  if (!process.env.PERIPLUS_QUERY_API_TOKEN)
    return Response.json({ detail: "Catalogue unavailable." }, { status: 503 });
  try {
    const result = await fetch(
      new URL(
        "/query/exec",
        process.env.PERIPLUS_QUERY_URL ?? "http://127.0.0.1:8010",
      ),
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${process.env.PERIPLUS_QUERY_API_TOKEN}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          sql: "SELECT count(*) AS captures FROM public_v1.capture;",
          parameters: [],
        }),
        cache: "no-store",
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(130000)]),
      },
    );
    return new Response(result.body, {
      status: result.status,
      headers: {
        "content-type": "application/json",
        "cache-control": "no-store",
      },
    });
  } catch {
    return Response.json({ detail: "Catalogue unavailable." }, { status: 503 });
  }
}
