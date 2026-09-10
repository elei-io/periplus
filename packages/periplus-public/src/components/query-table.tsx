"use client"

import { memo, type ReactNode } from "react"
import { ArrowUpRight } from "lucide-react"
import { Table, TableHeader, TableHead, TableBody, TableRow, TableCell } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { displayValue, formatQueryTimestamp } from "@/lib/query-values"

const numericType = /^(U?(?:TINY|SMALL|BIG|HUGE)?INT(?:EGER)?|DECIMAL|NUMERIC|DOUBLE|FLOAT|REAL)(?:\(\d+(?:,\s*\d+)?\))?$/i
const temporalType = /^(DATE|TIMESTAMP(?:_(?:S|MS|NS))?|TIMESTAMPTZ|TIMESTAMP WITH(?:OUT)? TIME ZONE)$/i

function columnKind(type: string, rows: unknown[][], column: number) {
  if (numericType.test(type)) return "number"
  if (temporalType.test(type)) return "date"
  let populated = false
  for (const row of rows) {
    const value = row[column]
    if (value === null || value === undefined) continue
    if (typeof value !== "string" || !/^https?:\/\//i.test(value)) return "text"
    populated = true
  }
  return populated ? "url" : "text"
}

export function QueryValue({ value, type = "" }: { value: unknown; type?: string }) {
  if (value === null || value === undefined) return <span className="data-null">NULL</span>
  if (value === "") return <span className="data-null">Empty string</span>
  if (typeof value === "string" && temporalType.test(type)) {
    const timestamp = formatQueryTimestamp(value, type)
    if (timestamp) return <time className="data-date" dateTime={timestamp.dateTime} title={value}>{timestamp.date}{timestamp.time && <span>{timestamp.time}</span>}</time>
  }
  if (typeof value === "string" && /^https?:\/\//i.test(value)) {
    let url: URL
    try {
      url = new URL(value)
    } catch { return value }
    return <a className="data-source" href={value} target="_blank" rel="noopener noreferrer" title={`${value}\nOpens the live website; it may differ from the capture.`} aria-label={`Open source: ${value} (live website)`}><span>{url.host}<ArrowUpRight aria-hidden="true" /></span><span>{url.pathname}{url.search}{url.hash}</span></a>
  }
  return displayValue(value)
}

export const QueryTable = memo(function QueryTable({ columns, types, rows, label = "Query results", renderCell, showTypes = true, imageColumns = [] }: {
  columns: string[]
  types: string[]
  rows: unknown[][]
  label?: string
  showTypes?: boolean
  imageColumns?: number[]
  renderCell?: (value: unknown, column: number) => ReactNode
}) {
  const kinds = columns.map((_, i) => imageColumns.includes(i) ? "image" : columnKind(types[i] ?? "", rows, i))
  return <div className="query-table-region">
    <div className="analysis-table" role="region" aria-label={`${label}. Scroll to see more rows or columns.`} tabIndex={0}>
      <Table aria-label={label}>
        <TableHeader><TableRow>{columns.map((column, i) => <TableHead key={i} scope="col" data-kind={kinds[i]}>{column}{showTypes && types[i] && <span className="column-type" title={types[i]}>{types[i]}</span>}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{rows.map((row, i) => <TableRow key={i}>{row.map((value, j) => <TableCell key={j} data-kind={kinds[j]} className={kinds[j] === "image" ? "w-24 min-w-24!" : undefined}>{renderCell ? renderCell(value, j) : <QueryValue value={value} type={types[j]} />}</TableCell>)}</TableRow>)}</TableBody>
      </Table>
    </div>
    <p className="table-scroll-hint">Scroll the table to explore all columns.</p>
  </div>
})

export function QueryTableLoading() {
  return <div className="query-loading" aria-hidden="true">{[0, 1, 2, 3].map(row => <div key={row}><Skeleton /><Skeleton /><Skeleton /></div>)}</div>
}
