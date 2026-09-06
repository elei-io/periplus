export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL"
  if (typeof value === "object") return JSON.stringify(value, (_, v) => typeof v === "bigint" ? String(v) : v)
  return String(value)
}

const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })
const timeFormat = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZone: "UTC" })

export function formatQueryTimestamp(value: string, type: string) {
  if (!/^(DATE|TIMESTAMP(?:_(?:S|MS|NS))?|TIMESTAMPTZ|TIMESTAMP WITH(?:OUT)? TIME ZONE)$/i.test(type)) return null
  const dateOnly = type.toUpperCase() === "DATE"
  const normalized = value.replace(" ", "T")
  const hasOffset = !dateOnly && /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i.test(normalized)
  const instant = new Date(hasOffset || dateOnly ? normalized : `${normalized}Z`)
  if (Number.isNaN(instant.getTime())) return null
  return {
    date: dateFormat.format(instant),
    time: dateOnly ? null : `${timeFormat.format(instant)}${hasOffset ? " UTC" : ""}`,
    // A timestamp without a timezone is a wall-clock value, not a UTC instant.
    dateTime: hasOffset ? instant.toISOString() : normalized,
  }
}
