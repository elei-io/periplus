import type { StorageSource } from "../../types/storage"

export function bytes(value: number | null | undefined) {
  if (value == null) return "—"
  if (value === 0) return "0 B"
  if (value < 1) return "<1 B"
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 5)
  return `${(value / 1024 ** unit).toLocaleString(undefined, { maximumFractionDigits: unit === 0 ? 0 : 1 })} ${["B", "KiB", "MiB", "GiB", "TiB", "PiB"][unit]}`
}

export function accountedBytes(sources: StorageSource[]) {
  const measured = sources.filter((source) => source.bytes !== null)
  return measured.length
    ? measured.reduce((sum, source) => sum + (source.bytes ?? 0), 0)
    : null
}

export function perObservation(value: number, observations: number) {
  return observations > 0 ? value / observations : null
}
