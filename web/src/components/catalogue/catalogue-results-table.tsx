import { memo, useCallback, useMemo, useRef, useState } from "react"
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react"
import { useVirtualizer } from "@tanstack/react-virtual"

import type { CatalogueQueryResult } from "@/types/catalogue"

const defaultColumnWidth = 220
const minimumColumnWidth = 96
const maximumColumnWidth = 960
const renderedValueLimit = 2_000

function displayValue(value: unknown) {
  if (value === null || value === undefined) return "NULL"
  if (typeof value === "object") {
    if (value instanceof Uint8Array) return Array.from(value).join("")
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

function renderedValue(value: unknown) {
  const full = displayValue(value)
  if (full.length <= renderedValueLimit) {
    return { preview: full, title: full }
  }
  return {
    preview: `${full.slice(0, renderedValueLimit)}…`,
    title: `${full.slice(0, 240)}… (${full.length.toLocaleString()} characters)`,
  }
}

function clampColumnWidth(width: number) {
  return Math.min(maximumColumnWidth, Math.max(minimumColumnWidth, width))
}

type CatalogueRowProps = {
  columns: string[]
  gridTemplateColumns: string
  row: unknown[]
  size: number
  start: number
}

const CatalogueRow = memo(function CatalogueRow({
  columns,
  gridTemplateColumns,
  row,
  size,
  start,
}: CatalogueRowProps) {
  return (
    <div
      className="absolute top-0 left-0 grid w-full border-b text-xs hover:bg-muted/40"
      style={{
        gridTemplateColumns,
        height: `${size}px`,
        transform: `translateY(${start}px)`,
      }}
    >
      {row.map((value, index) => {
        const rendered = renderedValue(value)
        return (
          <div
            key={`${columns[index]}-${index}`}
            className="min-w-0 truncate border-r px-3 py-2 font-mono"
            title={rendered.title}
          >
            {rendered.preview}
          </div>
        )
      })}
    </div>
  )
})

export function CatalogueResultsTable({
  result,
}: {
  result: CatalogueQueryResult
}) {
  const parentRef = useRef<HTMLDivElement>(null)
  const [columnWidths, setColumnWidths] = useState<Array<number | null>>(() =>
    result.columns.map(() => null)
  )
  // TanStack Virtual exposes mutable callbacks by design; the table itself stays uncompiled.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: result.rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 36,
    overscan: 12,
  })
  const tableMinimumWidth = columnWidths.reduce<number>(
    (total, width) => total + (width ?? minimumColumnWidth),
    0
  )
  const gridTemplateColumns = useMemo(
    () =>
      columnWidths
        .map((width) =>
          width === null
            ? `minmax(${minimumColumnWidth}px, 1fr)`
            : `${width}px`
        )
        .join(" "),
    [columnWidths]
  )

  const setColumnWidth = useCallback((index: number, width: number | null) => {
    setColumnWidths((current) => {
      const next = [...current]
      next[index] = width === null ? null : clampColumnWidth(width)
      return next
    })
  }, [])

  const startResize = useCallback(
    (index: number, event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault()
      const cell = event.currentTarget.parentElement
      const startWidth = cell?.getBoundingClientRect().width ?? defaultColumnWidth
      const startX = event.clientX
      const previousCursor = document.body.style.cursor
      const previousSelection = document.body.style.userSelect

      function move(moveEvent: PointerEvent) {
        setColumnWidth(index, startWidth + moveEvent.clientX - startX)
      }

      function stop() {
        document.removeEventListener("pointermove", move)
        document.removeEventListener("pointerup", stop)
        document.body.style.cursor = previousCursor
        document.body.style.userSelect = previousSelection
      }

      document.body.style.cursor = "col-resize"
      document.body.style.userSelect = "none"
      document.addEventListener("pointermove", move)
      document.addEventListener("pointerup", stop, { once: true })
    },
    [setColumnWidth]
  )

  const resizeWithKeyboard = useCallback(
    (index: number, event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
      event.preventDefault()
      const currentWidth =
        event.currentTarget.parentElement?.getBoundingClientRect().width ??
        defaultColumnWidth
      const direction = event.key === "ArrowLeft" ? -1 : 1
      setColumnWidth(index, currentWidth + direction * 16)
    },
    [setColumnWidth]
  )

  const gridStyle = {
    gridTemplateColumns,
    width: `max(100%, ${tableMinimumWidth}px)`,
  } satisfies CSSProperties

  return (
    <div
      ref={parentRef}
      data-testid="catalogue-results-scroller"
      className="h-[70svh] max-h-[48rem] min-h-80 min-w-0 max-w-full shrink-0 overflow-auto rounded-md border bg-background"
    >
      <div
        className="sticky top-0 z-10 grid w-full border-b bg-muted/95 text-xs font-medium backdrop-blur"
        style={gridStyle}
      >
        {result.columns.map((column, index) => (
          <div
            key={`${column}-${index}`}
            className="relative min-w-0 border-r px-3 py-2"
            title={`${column} · ${result.columnTypes[index]}`}
          >
            <div className="truncate pr-2">{column}</div>
            <div className="mt-0.5 truncate pr-2 font-mono text-[9px] font-normal tracking-wide text-muted-foreground uppercase">
              {result.columnTypes[index]}
            </div>
            <div
              role="separator"
              aria-label={`Resize ${column} column`}
              aria-orientation="vertical"
              tabIndex={0}
              className="absolute inset-y-0 right-0 z-20 w-2 translate-x-1/2 cursor-col-resize touch-none outline-none after:absolute after:inset-y-1.5 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-border hover:after:bg-primary focus-visible:after:w-0.5 focus-visible:after:bg-primary"
              onDoubleClick={() => setColumnWidth(index, null)}
              onKeyDown={(event) => resizeWithKeyboard(index, event)}
              onPointerDown={(event) => startResize(index, event)}
            />
          </div>
        ))}
      </div>
      <div
        className="relative"
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: `max(100%, ${tableMinimumWidth}px)`,
        }}
      >
        {virtualizer.getVirtualItems().map((item) => (
          <CatalogueRow
            key={item.key}
            columns={result.columns}
            gridTemplateColumns={gridTemplateColumns}
            row={result.rows[item.index]}
            size={item.size}
            start={item.start}
          />
        ))}
      </div>
    </div>
  )
}
