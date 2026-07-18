export function formatArrowValue(type: string, value: unknown): unknown {
  if (value === null || value === undefined || !type.startsWith("Timestamp<")) {
    return value
  }
  if (value instanceof Date) return value.toISOString()
  if (typeof value === "number") return new Date(value).toISOString()
  if (typeof value === "bigint") {
    const unit = type.slice("Timestamp<".length).split(",", 1)[0]
    const milliseconds =
      unit === "SECOND"
        ? value * 1_000n
        : unit === "MILLISECOND"
          ? value
          : unit === "MICROSECOND"
            ? value / 1_000n
            : value / 1_000_000n
    return new Date(Number(milliseconds)).toISOString()
  }
  return value
}
