export function truncateMiddle(value: string, maxLength = 88) {
  if (value.length <= maxLength) {
    return value
  }

  const marker = "..."
  const tailLength = Math.min(32, Math.floor(maxLength * 0.4))
  const headLength = Math.max(0, maxLength - tailLength - marker.length)

  return `${value.slice(0, headLength)}${marker}${value.slice(-tailLength)}`
}
