import { useRef } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"

import type { CatalogueQueryResult } from "@/types/catalogue"

function displayValue(value: unknown) {
  if (value === null || value === undefined) return "NULL"
  if (typeof value === "object") {
    if (value instanceof Uint8Array) return Array.from(value).join("")
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  return String(value)
}

export function CatalogueResultsTable({ result }: { result: CatalogueQueryResult }) {
  const parentRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: result.rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 36,
    overscan: 12,
  })
  const width = Math.max(160, 100 / Math.max(1, result.columns.length))

  return (
    <div ref={parentRef} className="min-h-0 flex-1 overflow-auto rounded-md border bg-background">
      <div className="sticky top-0 z-10 flex min-w-max border-b bg-muted/95 text-xs font-medium backdrop-blur">
        {result.columns.map((column) => (
          <div key={column} className="w-56 shrink-0 truncate border-r px-3 py-2.5" style={{ minWidth: `${width}px` }} title={column}>{column}</div>
        ))}
      </div>
      <div className="relative min-w-max" style={{ height: `${virtualizer.getTotalSize()}px` }}>
        {virtualizer.getVirtualItems().map((item) => {
          const row = result.rows[item.index]
          return (
            <div key={item.key} className="absolute left-0 top-0 flex border-b text-xs hover:bg-muted/40" style={{ height: `${item.size}px`, transform: `translateY(${item.start}px)` }}>
              {row.map((value, index) => {
                const text = displayValue(value)
                return <div key={result.columns[index]} className="w-56 shrink-0 truncate border-r px-3 py-2 font-mono" style={{ minWidth: `${width}px` }} title={text}>{text}</div>
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}
