"use client"

import { ChevronDown, Copy, Download } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { extractApiError } from "@/lib/api"
import { serializeQueryResults } from "@/lib/query-export"
import type { QueryResult } from "@/types/sql"
import { captureAnalytics } from "@/lib/analytics"

export function QueryExport({ result, disabled, operationId }: { result?: QueryResult; disabled: boolean; operationId?: string }) {
  async function exportResults(format: "csv" | "json", clipboard: boolean) {
    if (!result) return
    try {
      const content = serializeQueryResults(result, format)
      if (clipboard) {
        await navigator.clipboard.writeText(content)
        toast.success(`${result.rows.length} rows copied as ${format.toUpperCase()}.${result.truncated ? " Partial result only." : ""}`)
      } else {
        const address = URL.createObjectURL(new Blob([content], { type: format === "csv" ? "text/csv;charset=utf-8" : "application/json;charset=utf-8" }))
        const link = document.createElement("a")
        link.href = address
        link.download = `periplus-results.${format}`
        document.body.appendChild(link)
        link.click()
        link.remove()
        setTimeout(() => URL.revokeObjectURL(address), 1000)
      }
      captureAnalytics("sql_results_exported", {
        flow: "sql", operation_id: operationId, query_id: result.query_id,
        format,
        method: clipboard ? "clipboard" : "download",
        row_count: result.rows.length,
        truncated: result.truncated ?? false,
      })
    } catch (error) { toast.error(extractApiError(error)) }
  }
  return <DropdownMenu>
    <DropdownMenuTrigger disabled={disabled || !result} render={<Button variant="outline" size="sm" />}><Download />Export<ChevronDown /></DropdownMenuTrigger>
    <DropdownMenuContent align="end" className="w-52">
      <DropdownMenuItem onClick={() => exportResults("csv", false)}><Download />Download CSV</DropdownMenuItem>
      <DropdownMenuItem onClick={() => exportResults("json", false)}><Download />Download JSON</DropdownMenuItem>
      <DropdownMenuSeparator />
      <DropdownMenuItem onClick={() => exportResults("csv", true)}><Copy />Copy as CSV</DropdownMenuItem>
      <DropdownMenuItem onClick={() => exportResults("json", true)}><Copy />Copy as JSON</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
}
