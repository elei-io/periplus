import "server-only";

export async function admitPublic(
  feature: "assistant" | "sql",
  signal: AbortSignal,
  consume = true,
): Promise<Response | null> {
  const token = process.env.PERIPLUS_PUBLIC_API_TOKEN;
  if (!token)
    return Response.json(
      {
        code: "access_unavailable",
        detail: "Public access settings are unavailable.",
      },
      { status: 503 },
    );
  try {
    const result = await fetch(
      new URL(
        `/access/admit/${feature}`,
        process.env.PERIPLUS_API_URL ?? "http://127.0.0.1:8000",
      ),
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${token}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ consume }),
        cache: "no-store",
        signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
      },
    );
    if (result.ok) return null;
    const body = await result.json();
    return Response.json(typeof body.detail === "object" ? body.detail : body, {
      status: result.status,
      headers: {
        "cache-control": "no-store",
        ...(result.headers.has("retry-after")
          ? { "retry-after": result.headers.get("retry-after")! }
          : {}),
      },
    });
  } catch {
    return Response.json(
      {
        code: "access_unavailable",
        detail: "Public access settings are temporarily unavailable.",
      },
      { status: 503 },
    );
  }
}
