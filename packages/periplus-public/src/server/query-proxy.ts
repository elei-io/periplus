import "server-only"
import type { QueryMode } from "@/types/sql"
import { admitPublic } from "./public-access";
import { beginOperation } from "./telemetry";

// Transport only. Python owns query policy, preparation and execution.
export async function proxyQuery(request: Request, path: string, mode: QueryMode = "stable") {
  const finish = beginOperation("query_proxy", request.headers.get("x-periplus-operation-id"));
  if (path === "/query/exec" || path === "/query/prep") {
    const denial = await admitPublic("sql", request.signal, path === "/query/exec")
    if (denial) { finish(denial.status >= 500 ? "failed" : "rejected"); return denial }
  }
  const baseUrl = mode === "experimental" ? process.env.PERIPLUS_QUERY_EXPERIMENTAL_URL : process.env.PERIPLUS_QUERY_URL ?? "http://127.0.0.1:8010";
  const token = process.env.PERIPLUS_QUERY_API_TOKEN;
  if (!token || !baseUrl) {
    finish("unconfigured");
    return Response.json(
      { detail: "Periplus connection is not configured." },
      { status: 503 },
    );
  }
  // Client attribution is analytics only; never forward arbitrary privileged source labels.
  const headers = new Headers({
    authorization: `Bearer ${token}`,
    "x-periplus-query-source": request.headers.get("x-periplus-query-source") === "sdk" ? "sdk" : "public_console",
  });
  for (const name of ["content-type", "range"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const upstream = await fetch(
      new URL(path, baseUrl),
      {
        method: request.method,
        headers,
        body: request.method === "POST" ? request.body : undefined,
        ...(request.method === "POST" ? { duplex: "half" } : {}),
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(130_000)]),
        cache: "no-store",
      },
    );
    const responseHeaders = new Headers();
    for (const name of [
      "content-type",
      "content-length",
      "content-range",
      "accept-ranges",
      "cache-control",
      "retry-after",
    ]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    const outcome = upstream.ok
      ? "success"
      : upstream.status === 429
        ? "rejected"
        : "failed";
    if (!upstream.body) {
      finish(outcome);
      return new Response(null, {
        status: upstream.status,
        headers: responseHeaders,
      });
    }
    const reader = upstream.body.getReader();
    const body = new ReadableStream({
      async pull(controller) {
        try {
          const result = await reader.read();
          if (result.done) {
            finish(outcome);
            controller.close();
          } else controller.enqueue(result.value);
        } catch {
          finish(request.signal.aborted ? "cancelled" : "failed");
          controller.error(new Error("Query response stream failed"));
        }
      },
      async cancel() {
        finish("cancelled");
        await reader.cancel();
      },
    });
    return new Response(body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    finish(request.signal.aborted ? "cancelled" : "failed");
    return Response.json(
      { detail: "Periplus is temporarily unavailable." },
      { status: 503 },
    );
  }
}
