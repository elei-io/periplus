import {
  executePeriplusQuery,
  PeriplusQueryError,
} from "@/server/periplus-query-client"

const MAX_SQL_BYTES = 100_000

function sqlFromBody(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if (Object.keys(record).some((key) => key !== "sql")) return null
  return typeof record.sql === "string" ? record.sql : null
}

export async function POST(request: Request) {
  const contentLength = Number(request.headers.get("content-length"))
  if (Number.isFinite(contentLength) && contentLength > MAX_SQL_BYTES + 1_000) {
    return Response.json({ detail: "SQL request is too large." }, { status: 413 })
  }

  const sql = sqlFromBody(await request.json().catch(() => null))
  if (
    !sql?.trim() ||
    new TextEncoder().encode(sql).byteLength > MAX_SQL_BYTES
  ) {
    return Response.json(
      { detail: "Request body must contain one non-empty SQL string." },
      { status: 400 }
    )
  }

  try {
    return Response.json(await executePeriplusQuery(sql, request.signal))
  } catch (error) {
    if (error instanceof PeriplusQueryError) {
      return Response.json({ detail: error.message }, { status: error.status })
    }
    if (error instanceof Error && error.name === "TimeoutError") {
      return Response.json(
        { detail: "Periplus query timed out." },
        { status: 504 }
      )
    }
    return Response.json(
      { detail: "Periplus API is unavailable." },
      { status: 503 }
    )
  }
}
