export function age(value: string, asOf: string) {
  const seconds = Math.max(0, Math.floor((Date.parse(asOf) - Date.parse(value)) / 1000))
  return seconds < 60 ? `${seconds}s ago` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago` : seconds < 86400 ? `${Math.floor(seconds / 3600)}h ago` : `${Math.floor(seconds / 86400)}d ago`
}
