import {
  InvalidDomainQueryError,
  listDomains,
} from "@/server/queries/domains"
import { PeriplusError } from "@/server/periplus-client"

const DEFAULT_LIMIT = 25
const MAX_LIMIT = 100
const ALLOWED_PARAMETERS = new Set(["search", "cursor", "limit"])

export async function GET(request: Request) {
  const parameters = new URL(request.url).searchParams
  if (
    [...parameters.keys()].some((key) => !ALLOWED_PARAMETERS.has(key)) ||
    [...ALLOWED_PARAMETERS].some((key) => parameters.getAll(key).length > 1)
  ) {
    return Response.json(
      { detail: "Domain query parameters are invalid." },
      { status: 400 }
    )
  }

  const limitValue = parameters.get("limit")
  const limit = limitValue === null ? DEFAULT_LIMIT : Number(limitValue)
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
    return Response.json(
      { detail: `limit must be between 1 and ${MAX_LIMIT}.` },
      { status: 400 }
    )
  }

  try {
    const response = await listDomains({
      search: parameters.get("search") ?? "",
      cursor: parameters.get("cursor"),
      limit,
    })
    return Response.json(response, {
      headers: { "cache-control": "private, no-store" },
    })
  } catch (error) {
    if (error instanceof InvalidDomainQueryError) {
      return Response.json({ detail: error.message }, { status: 400 })
    }
    if (error instanceof PeriplusError) {
      return Response.json({ detail: error.message }, { status: error.status })
    }
    if (error instanceof Error && error.name === "TimeoutError") {
      return Response.json(
        { detail: "Domain query timed out." },
        { status: 504 }
      )
    }
    return Response.json(
      { detail: "Domain catalogue is unavailable." },
      { status: 503 }
    )
  }
}
