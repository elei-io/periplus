export type ParameterKind = "text" | "number" | "boolean" | "null" | "json"
export type ParameterDraft = { kind: ParameterKind; value: string }

export function parameterDraft(value: unknown): ParameterDraft {
  if (value === null) return { kind: "null", value: "" }
  if (typeof value === "string") return { kind: "text", value }
  if (typeof value === "number") return { kind: "number", value: String(value) }
  if (typeof value === "boolean") return { kind: "boolean", value: String(value) }
  return { kind: "json", value: JSON.stringify(value) }
}

export function parameterValue(row: ParameterDraft): unknown {
  if (row.kind === "text") return row.value
  if (row.kind === "null") return null
  if (row.kind === "number") {
    if (!row.value.trim() || !Number.isFinite(Number(row.value))) throw new Error("Enter a finite number.")
    if (Number.isInteger(Number(row.value)) && !Number.isSafeInteger(Number(row.value))) throw new Error("Use Text for integers outside the safe numeric range.")
    return Number(row.value)
  }
  if (row.kind === "boolean") {
    if (!["true", "false"].includes(row.value)) throw new Error("Choose true or false.")
    return row.value === "true"
  }
  try { return JSON.parse(row.value) } catch { throw new Error("Enter valid JSON.") }
}

// CSV-style quoting preserves commas, newlines and explicit string values.
export function pasteParameters(input: string): ParameterDraft[] {
  if (!input.trim()) return []
  const fields: { value: string; quoted: boolean }[] = []
  let value = "", quoted = false, inQuote = false, closed = false
  const push = () => { fields.push({ value: quoted ? value : value.trim(), quoted }); value = ""; quoted = false; closed = false }
  for (let i = 0; i < input.length; i++) {
    const char = input[i]
    if (inQuote) {
      if (char === '"') {
        if (input[i + 1] === '"') { value += '"'; i++ }
        else { inQuote = false; closed = true }
      } else value += char
    } else if (char === ',' || char === '\n' || char === '\r') {
      push()
      if (char === '\r' && input[i + 1] === '\n') i++
    } else if (char === '"' && !value.trim() && !closed) {
      value = ""; quoted = true; inQuote = true
    } else if (closed) {
      if (char.trim()) throw new Error("Separate quoted values with a comma or a new line.")
    } else value += char
  }
  if (inQuote) throw new Error("Close the quoted value before importing.")
  if (value || quoted || !/[\r\n]$/.test(input)) push()
  return fields.map(field => {
    if (field.quoted) return { kind: "text", value: field.value }
    if (["true", "false"].includes(field.value)) return { kind: "boolean", value: field.value }
    if (field.value === "null") return { kind: "null", value: "" }
    if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(field.value)) {
      const number = Number(field.value)
      if (Number.isFinite(number) && (!Number.isInteger(number) || Number.isSafeInteger(number))) return { kind: "number", value: field.value }
    }
    return { kind: "text", value: field.value }
  })
}
