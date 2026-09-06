export interface PublicDomainSummary {
  hostname: string
  observationCount: number
  lastObservedAt: string | null
}

export interface DomainListResponse {
  items: PublicDomainSummary[]
  nextCursor: string | null
}

export function isDomainListResponse(
  value: unknown
): value is DomainListResponse {
  if (!value || typeof value !== "object") return false
  const candidate = value as Partial<DomainListResponse>
  return (
    Array.isArray(candidate.items) &&
    candidate.items.every(
      (item) =>
        item !== null &&
        typeof item === "object" &&
        typeof item.hostname === "string" &&
        Number.isSafeInteger(item.observationCount) &&
        item.observationCount >= 0 &&
        (item.lastObservedAt === null ||
          typeof item.lastObservedAt === "string")
    ) &&
    (candidate.nextCursor === null ||
      typeof candidate.nextCursor === "string")
  )
}

export interface ObservedUrlSummary {
  urlReference: string
  url: string
  hostname: string
  observationCount: number
  lastObservedAt: string | null
  latestOutcome: string | null
  latestHttpStatusCode: number | null
}
