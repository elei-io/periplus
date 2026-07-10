export type CacheMode = "prefer" | "refresh" | "no_store"

export type CacheOptions = {
  mode?: CacheMode
  max_age_seconds?: number
  stale_if_error_seconds?: number
}
