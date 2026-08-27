export interface PublicDomainSummary {
  hostname: string
  knownUrlCount: number
  observationCount: number
  lastObservedAt: string | null
  retainedContentCount: number
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
