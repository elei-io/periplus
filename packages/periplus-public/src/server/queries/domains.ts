import "server-only"

import { executePeriplusQuery } from "@/server/periplus-query-client"
import type {
  DomainListResponse,
  PublicDomainSummary,
} from "@/types/public-catalogue"

const DOMAIN_COLUMNS = [
  "hostname",
  "observation_count",
  "last_observed_at",
] as const
const HOSTNAME_EXPRESSION = String.raw`regexp_extract(
  coalesce(effective_url, requested_url),
  '^https?://(\[[^]]+\]|[^/:]+)',
  1
)`

interface DomainCursor {
  version: 1
  hostname: string
}

export interface DomainListInput {
  search: string
  cursor: string | null
  limit: number
}

export class InvalidDomainQueryError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "InvalidDomainQueryError"
  }
}

export async function listDomains({
  search,
  cursor,
  limit,
}: DomainListInput): Promise<DomainListResponse> {
  const normalizedSearch = normalizeHostnameInput(search, "search")
  const afterHostname = cursor ? decodeCursor(cursor) : null
  const predicates = ["hostname <> ''"]
  if (normalizedSearch) {
    predicates.push(
      `starts_with(hostname, ${sqlString(normalizedSearch)})`
    )
  }
  if (afterHostname) {
    predicates.push(`hostname > ${sqlString(afterHostname)}`)
  }

  const result = await executePeriplusQuery(`
    WITH domain_observations AS (
      SELECT ${HOSTNAME_EXPRESSION} AS hostname,
             observed_at
      FROM web.observation
    )
    SELECT hostname,
           count(*) AS observation_count,
           max(observed_at) AS last_observed_at
    FROM domain_observations
    WHERE ${predicates.join("\n      AND ")}
    GROUP BY hostname
    ORDER BY hostname
    LIMIT ${limit + 1}
  `)

  if (
    result.columns.length !== DOMAIN_COLUMNS.length ||
    !result.columns.every((column, index) => column === DOMAIN_COLUMNS[index])
  ) {
    throw new Error("Periplus returned an incompatible domain query result.")
  }

  const rows = result.rows.map(domainFromRow)
  const hasNextPage = rows.length > limit
  const items = rows.slice(0, limit)
  return {
    items,
    nextCursor:
      hasNextPage && items.length
        ? encodeCursor(items[items.length - 1]!.hostname)
        : null,
  }
}

function domainFromRow(row: unknown[]): PublicDomainSummary {
  const [hostname, observationCount, lastObservedAt] = row
  if (
    typeof hostname !== "string" ||
    !Number.isSafeInteger(observationCount) ||
    Number(observationCount) < 0 ||
    (lastObservedAt !== null && typeof lastObservedAt !== "string")
  ) {
    throw new Error("Periplus returned an invalid domain row.")
  }
  return {
    hostname,
    observationCount: Number(observationCount),
    lastObservedAt,
  }
}

function normalizeHostnameInput(value: string, label: string): string {
  const normalized = value.trim().toLowerCase()
  if (normalized.length > 253) {
    throw new InvalidDomainQueryError(`${label} is too long.`)
  }
  if (/[\s/?#@\\']/u.test(normalized)) {
    throw new InvalidDomainQueryError(`${label} is not a hostname prefix.`)
  }
  return normalized
}

function encodeCursor(hostname: string): string {
  const cursor: DomainCursor = { version: 1, hostname }
  return Buffer.from(JSON.stringify(cursor), "utf8").toString("base64url")
}

function decodeCursor(value: string): string {
  try {
    const parsed: unknown = JSON.parse(
      Buffer.from(value, "base64url").toString("utf8")
    )
    if (
      !parsed ||
      typeof parsed !== "object" ||
      (parsed as Partial<DomainCursor>).version !== 1 ||
      typeof (parsed as Partial<DomainCursor>).hostname !== "string"
    ) {
      throw new Error("invalid cursor")
    }
    return normalizeHostnameInput(
      (parsed as DomainCursor).hostname,
      "cursor"
    )
  } catch (error) {
    if (error instanceof InvalidDomainQueryError) throw error
    throw new InvalidDomainQueryError("cursor is invalid.")
  }
}

function sqlString(value: string): string {
  return `'${value.replaceAll("'", "''")}'`
}
