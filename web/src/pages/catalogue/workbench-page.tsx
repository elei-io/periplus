import { Prec } from "@codemirror/state"
import { EditorView } from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import { useEffect, useMemo, useRef, useState } from "react"
import {
  BracesIcon,
  CopyIcon,
  FileCode2Icon,
  LockKeyholeIcon,
  SaveIcon,
  TriangleAlertIcon,
  ViewIcon,
} from "lucide-react"
import { toast } from "sonner"

import { createCatalogueCompletionExtensions } from "@/components/catalogue/catalogue-completions"
import { SaveQueryDialog } from "@/components/catalogue/save-query-dialog"
import { SaveTableMacroDialog } from "@/components/catalogue/save-table-macro-dialog"
import { SaveViewDialog } from "@/components/catalogue/save-view-dialog"
import {
  isWorkbenchCommandLike,
  parseWorkbenchCommand,
  runWorkbenchCommand,
  workbenchCommandSuggestion,
  WORKBENCH_COMMANDS,
} from "@/components/catalogue/workbench-commands"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useCatalogueLint } from "@/hooks/use-catalogue-lint"
import { useCatalogueMetadata } from "@/hooks/use-catalogue-metadata"
import { useCatalogueQuery } from "@/hooks/use-catalogue-query"
import { useCatalogueStatus } from "@/hooks/use-catalogue-status"
import { extractApiError } from "@/lib/api"
import type {
  CatalogueLintDiagnostic,
  CatalogueQueryResult,
} from "@/types/catalogue"

type TranscriptEntry = {
  id: string
  sql: string
  status: "running" | "success" | "error"
  result?: CatalogueQueryResult
  output?: "help" | "welcome" | "message"
  message?: string
  expanded?: boolean
  error?: string
  durationMs?: number
}

type SaveKind = "view" | "query" | "macro"

type SaveTarget = {
  kind: SaveKind
  sql: string
}

const SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
const RUNNING_INDICATOR_DELAY_MS = 200

function TerminalSpinner() {
  const [frame, setFrame] = useState(0)

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return

    const interval = window.setInterval(
      () => setFrame((current) => (current + 1) % SPINNER_FRAMES.length),
      80
    )
    return () => window.clearInterval(interval)
  }, [])

  return (
    <span aria-hidden="true" className="inline-block w-[1ch]">
      {SPINNER_FRAMES[frame]}
    </span>
  )
}

function DelayedRunningIndicator({ compact = false }: { compact?: boolean }) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const timeout = window.setTimeout(
      () => setVisible(true),
      RUNNING_INDICATOR_DELAY_MS
    )
    return () => window.clearTimeout(timeout)
  }, [])

  if (!visible) return null

  return (
    <span
      role="status"
      aria-label="Query running"
      className={`${compact ? "ml-2 inline-flex align-middle" : "flex"} items-center gap-1.5 text-amber-600 dark:text-amber-300/80`}
    >
      <TerminalSpinner />
      {!compact && <span>RUNNING</span>}
    </span>
  )
}

const workbenchPromptTheme = Prec.highest(
  EditorView.theme({
    "&": {
      backgroundColor: "transparent",
      color: "var(--foreground)",
      fontSize: "13px",
    },
    "&.cm-focused": { outline: "none" },
    ".cm-scroller": {
      fontFamily:
        "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      lineHeight: "24px",
      overflow: "auto",
    },
    ".cm-content": {
      minHeight: "12rem",
      padding: "4px 0",
      caretColor: "transparent",
    },
    ".cm-line": { padding: "0" },
    ".cm-gutters": { display: "none" },
    ".cm-activeLine": { backgroundColor: "transparent" },
    ".cm-cursorLayer": { animation: "none !important" },
    ".cm-cursor, .cm-dropCursor": {
      animation: "none !important",
      backgroundColor: "var(--primary)",
      borderLeft: "0 !important",
      display: "block",
      width: "0.62em",
    },
    ".cm-selectionBackground": {
      backgroundColor: "var(--sql-selection) !important",
    },
    ".cm-tooltip.atlas-terminal-completions": {
      visibility: "hidden",
      pointerEvents: "none",
    },
    ".cm-atlasGhostText": {
      color: "var(--muted-foreground)",
      opacity: "0.5",
      pointerEvents: "none",
    },
  })
)

function PromptPrefix({ continuation = false }: { continuation?: boolean }) {
  return (
    <div
      aria-hidden="true"
      className={`flex h-6 w-36 shrink-0 items-center gap-1.5 text-[13px] leading-6 ${continuation ? "justify-end" : "justify-start"}`}
    >
      {continuation ? (
        <>
          <span className="text-muted-foreground/60">...</span>
          <span className="ml-1 text-primary">›</span>
        </>
      ) : (
        <>
          <span className="font-semibold text-primary">atlas</span>
          <span className="text-muted-foreground/50">:</span>
          <span className="text-muted-foreground">catalogue</span>
          <span className="ml-1 text-primary">›</span>
        </>
      )}
    </div>
  )
}

function PromptGutter({ value }: { value: string }) {
  const lineCount = value.split("\n").length

  return (
    <div className="shrink-0 pt-1">
      {Array.from({ length: lineCount }, (_, index) => (
        <PromptPrefix key={index} continuation={index > 0} />
      ))}
    </div>
  )
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "NULL"
  if (typeof value === "bigint") return value.toString()
  if (typeof value === "object") {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

function jsonValue(value: unknown): unknown {
  if (typeof value === "bigint") return value.toString()
  if (value instanceof Uint8Array) return Array.from(value)
  if (Array.isArray(value)) return value.map(jsonValue)
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, jsonValue(item)])
    )
  }
  return value ?? null
}

function uniqueColumnNames(columns: string[]) {
  const counts = new Map<string, number>()
  return columns.map((column) => {
    const count = (counts.get(column) ?? 0) + 1
    counts.set(column, count)
    return count === 1 ? column : `${column}_${count}`
  })
}

function resultAsJson(result: CatalogueQueryResult) {
  const columns = uniqueColumnNames(result.columns)
  return JSON.stringify(
    result.rows.map((row) =>
      Object.fromEntries(
        columns.map((column, index) => [column, jsonValue(row[index])])
      )
    ),
    null,
    2
  )
}

function csvCell(value: unknown) {
  const normalized =
    value === null || value === undefined
      ? ""
      : typeof value === "object"
        ? JSON.stringify(jsonValue(value))
        : String(value)
  return `"${normalized.replaceAll('"', '""')}"`
}

function resultAsCsv(result: CatalogueQueryResult) {
  return [
    result.columns.map(csvCell).join(","),
    ...result.rows.map((row) => row.map(csvCell).join(",")),
  ].join("\r\n")
}

type ResultExportFormat = "csv" | "json"

function serializeResult(result: CatalogueQueryResult, format: ResultExportFormat) {
  return format === "csv" ? resultAsCsv(result) : resultAsJson(result)
}

function ResultExportActions({ result }: { result: CatalogueQueryResult }) {
  async function copy(format: ResultExportFormat) {
    try {
      await navigator.clipboard.writeText(serializeResult(result, format))
      toast.success(`${format.toUpperCase()} copied.`)
    } catch (error) {
      toast.error(extractApiError(error))
    }
  }

  function download(format: ResultExportFormat) {
    try {
      const contents = serializeResult(result, format)
      const blob = new Blob([contents], {
        type:
          format === "csv"
            ? "text/csv;charset=utf-8"
            : "application/json;charset=utf-8",
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = `atlas-results-${new Date().toISOString().replaceAll(":", "-")}.${format}`
      link.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      toast.error(extractApiError(error))
    }
  }

  return (
    <div className="flex justify-end gap-1 font-sans">
      <Select
        value={null}
        onValueChange={(value) =>
          value && void copy(value as ResultExportFormat)
        }
      >
        <SelectTrigger
          size="sm"
          aria-label="Copy table result"
          title="Copy table result"
          className="h-6 border-transparent bg-transparent px-1.5 hover:bg-muted/60 dark:bg-transparent dark:hover:bg-muted/40"
        >
          <CopyIcon />
        </SelectTrigger>
        <SelectContent align="end" alignItemWithTrigger={false}>
          <SelectItem value="csv">Copy as CSV</SelectItem>
          <SelectItem value="json">Copy as JSON</SelectItem>
        </SelectContent>
      </Select>
      <Select
        value={null}
        onValueChange={(value) =>
          value && download(value as ResultExportFormat)
        }
      >
        <SelectTrigger
          size="sm"
          aria-label="Download table result"
          title="Download table result"
          className="h-6 border-transparent bg-transparent px-1.5 hover:bg-muted/60 dark:bg-transparent dark:hover:bg-muted/40"
        >
          <SaveIcon />
        </SelectTrigger>
        <SelectContent align="end" alignItemWithTrigger={false}>
          <SelectItem value="csv">Download as CSV</SelectItem>
          <SelectItem value="json">Download as JSON</SelectItem>
        </SelectContent>
      </Select>
    </div>
  )
}

function isClearCommand(value: string) {
  return normalizeCommand(value) === "clear"
}

function normalizeCommand(value: string) {
  return value.trim().replace(/;$/, "").trim().toLowerCase()
}

function isTerminatedSql(value: string) {
  return value.trimEnd().endsWith(";")
}

function isLintableSql(value: string) {
  return (
    Boolean(value.trim()) &&
    !isClearCommand(value) &&
    normalizeCommand(value) !== "help" &&
    !isWorkbenchCommandLike(value)
  )
}

function isMetaCommand(value: string) {
  return normalizeCommand(value) === "help" || isWorkbenchCommandLike(value)
}

function runsOnEnter(value: string) {
  return isClearCommand(value) || isMetaCommand(value) || isTerminatedSql(value)
}

const MIN_COLUMN_WIDTH = 120
const INITIAL_COLUMN_WIDTH = 180

function HelpOutput() {
  return (
    <dl className="mt-2 space-y-1 font-mono text-xs">
      {WORKBENCH_COMMANDS.map(({ command, description }) => (
        <div key={command} className="flex gap-4">
          <dt className="w-28 shrink-0 text-primary">{command}</dt>
          <dd className="text-muted-foreground">{description}</dd>
        </div>
      ))}
    </dl>
  )
}

function WelcomeOutput({ schemaVersion }: { schemaVersion?: number }) {
  return (
    <div className="space-y-3 font-mono text-xs">
      <pre className="leading-5 text-foreground/80">
        {[
          "     ___  ________  ___   _____",
          "    / _ |/_  __/ / / _ | / ___/",
          "   / __ | / / / /_/ __ |(__  )",
          "  /_/ |_|/_/ /___/_/ |_/____/",
          "",
          "  --------------------------------",
        ].join("\n")}
      </pre>
      <div className="space-y-1 text-muted-foreground">
        <p className="text-foreground/80">SQL over the internet.</p>
        <p>Query crawled pages, documents, and DOM data.</p>
      </div>
      <p className="text-muted-foreground">
        Atlas schema v{schemaVersion ?? "—"} · type{" "}
        <span className="text-primary">\?</span> for help
      </p>
    </div>
  )
}

function TranscriptActions({
  sql,
  onSave,
}: {
  sql: string
  onSave: (kind: SaveKind, sql: string) => void
}) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(sql)
      toast.success("SQL copied.")
    } catch (error) {
      toast.error(extractApiError(error))
    }
  }

  return (
    <div className="ml-auto flex h-6 shrink-0 items-center gap-1 pl-3">
      <Select
        value={null}
        onValueChange={(value) => value && onSave(value as SaveKind, sql)}
      >
        <SelectTrigger
          size="sm"
          aria-label="Save command"
          title="Save query"
          className="h-6 border-transparent bg-transparent px-1.5 font-sans hover:bg-muted/60 dark:bg-transparent dark:hover:bg-muted/40"
        >
          <SaveIcon />
        </SelectTrigger>
        <SelectContent align="start" alignItemWithTrigger={false}>
          <SelectItem value="view">
            <ViewIcon />
            Save as view
          </SelectItem>
          <SelectItem value="query">
            <FileCode2Icon />
            Save as query
          </SelectItem>
          <SelectItem value="macro">
            <BracesIcon />
            Save as macro
          </SelectItem>
        </SelectContent>
      </Select>
      <Button
        type="button"
        variant="ghost"
        size="xs"
        aria-label="Copy SQL"
        title="Copy SQL"
        className="h-6 px-1.5 font-sans"
        onClick={() => void copy()}
      >
        <CopyIcon />
      </Button>
    </div>
  )
}

function FooterLintDiagnostics({
  diagnostics,
}: {
  diagnostics: CatalogueLintDiagnostic[]
}) {
  if (diagnostics.length === 0) return null

  const diagnostic =
    diagnostics.find((item) => item.severity === "error") ?? diagnostics[0]
  const errorCount = diagnostics.filter(
    (item) => item.severity === "error"
  ).length
  const count = errorCount || diagnostics.length
  const noun = errorCount > 0 ? "error" : "warning"

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            role="status"
            aria-live="polite"
            tabIndex={0}
            className={`flex min-w-0 items-center gap-1.5 outline-none ${diagnostic.severity === "error" ? "text-red-700/85 dark:text-red-300/75" : "text-amber-700/80 dark:text-amber-300/65"}`}
          />
        }
      >
        <TriangleAlertIcon aria-hidden="true" className="size-3 shrink-0" />
        <span className="shrink-0">
          {count} {noun}
          {count === 1 ? "" : "s"}
        </span>
      </TooltipTrigger>
      <TooltipContent
        side="top"
        align="start"
        className="max-w-md flex-col items-start font-mono text-[10px] whitespace-normal"
      >
        {diagnostics.map((item) => (
          <span key={item.code}>{item.message}</span>
        ))}
      </TooltipContent>
    </Tooltip>
  )
}

function ExpandedResult({
  result,
  durationMs,
}: {
  result: CatalogueQueryResult
  durationMs?: number
}) {
  return (
    <div className="mt-3 max-w-full font-mono text-xs">
      <div className="max-h-[min(42vh,24rem)] overflow-auto border-y border-border/80">
        {result.rows.map((row, rowIndex) => (
          <div
            key={rowIndex}
            className="border-b border-border/80 last:border-b-0"
          >
            <div className="bg-muted/30 px-3 py-1 text-[10px] text-muted-foreground">
              record {rowIndex + 1}
            </div>
            {result.columns.map((column, columnIndex) => (
              <div
                key={`${column}-${columnIndex}`}
                className="grid grid-cols-[minmax(8rem,16rem)_minmax(0,1fr)] even:bg-muted/[0.06]"
              >
                <div className="border-r border-border/80 px-3 py-1.5">
                  <span className="block truncate text-foreground/75">
                    {column}
                  </span>
                  <span className="block truncate text-[9px] text-muted-foreground/70">
                    {result.columnTypes[columnIndex] ?? "unknown"}
                  </span>
                </div>
                <div className="px-3 py-1.5 break-words whitespace-pre-wrap text-foreground/85">
                  {formatCell(row[columnIndex])}
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
      <p className="mt-2 text-[10px] text-muted-foreground">
        {result.rows.length} {result.rows.length === 1 ? "row" : "rows"}
        {durationMs !== undefined && ` · ${formatDuration(durationMs)}`}
      </p>
    </div>
  )
}

function ResultTable({
  result,
  durationMs,
  expanded = false,
}: {
  result: CatalogueQueryResult
  durationMs?: number
  expanded?: boolean
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [containerWidth, setContainerWidth] = useState(0)
  const [columnWidths, setColumnWidths] = useState(() =>
    result.columns.map(() => INITIAL_COLUMN_WIDTH)
  )
  const [resize, setResize] = useState<{
    index: number
    startX: number
    startWidth: number
  } | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width
      setContainerWidth(width)
      setColumnWidths((current) => {
        const currentWidth = current.reduce((total, value) => total + value, 0)
        if (current.length === 0 || currentWidth >= width) return current

        const extraWidth = (width - currentWidth) / current.length
        return current.map((value) => value + extraWidth)
      })
    })

    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  function resizeColumn(index: number, width: number) {
    setColumnWidths((current) =>
      current.map((value, currentIndex) =>
        currentIndex === index ? Math.max(MIN_COLUMN_WIDTH, width) : value
      )
    )
  }

  const tableWidth = Math.max(
    containerWidth,
    columnWidths.reduce((total, value) => total + value, 0)
  )

  if (expanded && result.rows.length > 0) {
    return (
      <div className="mt-3 max-w-full">
        <ResultExportActions result={result} />
        <ExpandedResult result={result} durationMs={durationMs} />
      </div>
    )
  }

  return (
    <div className="mt-3 max-w-full">
      <ResultExportActions result={result} />
      <div
        ref={containerRef}
        className="mt-1 max-h-[min(42vh,24rem)] w-full overflow-auto"
      >
        <table
          className="table-fixed border-separate border-spacing-0 bg-muted/[0.04] font-mono text-xs"
          style={{ width: `${tableWidth}px` }}
        >
          <colgroup>
            {result.columns.map((column, index) => (
              <col
                key={`${column}-${index}`}
                style={{ width: `${columnWidths[index]}px` }}
              />
            ))}
          </colgroup>
          <thead className="sticky top-0 z-10">
            <tr>
              {result.columns.map((column, index) => (
                <th
                  key={`${column}-${index}`}
                  className="relative border-y border-r bg-muted/45 px-3 py-2 text-left font-medium text-foreground/75 backdrop-blur-[2px] first:border-l"
                >
                  <span className="block truncate">{column}</span>
                  <span className="mt-0.5 block truncate text-[9px] leading-3 font-normal tracking-wide text-muted-foreground/70">
                    {result.columnTypes[index] ?? "unknown"}
                  </span>
                  <button
                    type="button"
                    aria-label={`Resize ${column} column`}
                    className="group absolute inset-y-0 -right-1 z-20 flex w-2 cursor-col-resize touch-none justify-center"
                    onPointerDown={(event) => {
                      event.currentTarget.setPointerCapture(event.pointerId)
                      setResize({
                        index,
                        startX: event.clientX,
                        startWidth: columnWidths[index],
                      })
                    }}
                    onPointerMove={(event) => {
                      if (!resize || resize.index !== index) return
                      resizeColumn(
                        index,
                        resize.startWidth + event.clientX - resize.startX
                      )
                    }}
                    onPointerUp={(event) => {
                      event.currentTarget.releasePointerCapture(event.pointerId)
                      setResize(null)
                    }}
                    onKeyDownCapture={(event) => {
                      if (
                        event.key !== "ArrowLeft" &&
                        event.key !== "ArrowRight"
                      ) {
                        return
                      }
                      event.preventDefault()
                      resizeColumn(
                        index,
                        columnWidths[index] +
                          (event.key === "ArrowRight" ? 16 : -16)
                      )
                    }}
                  >
                    <span className="h-full w-px bg-border transition-colors group-hover:bg-primary group-focus-visible:bg-primary" />
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="even:bg-muted/10 hover:bg-muted/15">
                {row.map((value, columnIndex) => {
                  const formatted = formatCell(value)
                  return (
                    <td
                      key={columnIndex}
                      title={formatted}
                      className="truncate border-r border-b px-3 py-1.5 text-foreground/85 first:border-l"
                    >
                      {formatted}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 font-mono text-[10px] text-muted-foreground">
        {result.rows.length} {result.rows.length === 1 ? "row" : "rows"}
        {durationMs !== undefined && ` · ${formatDuration(durationMs)}`}
      </p>
    </div>
  )
}

export function CatalogueWorkbenchPage() {
  const [input, setInput] = useState("")
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([
    {
      id: "welcome",
      sql: "",
      status: "success",
      output: "welcome",
    },
  ])
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState<number | null>(null)
  const [expandedOutput, setExpandedOutput] = useState(false)
  const [saveTarget, setSaveTarget] = useState<SaveTarget | null>(null)
  const historyDraftRef = useRef("")
  const editorRef = useRef<EditorView | null>(null)
  const transcriptEndRef = useRef<HTMLDivElement>(null)
  const catalogueQuery = useCatalogueQuery()
  const catalogueLint = useCatalogueLint(input, "run", isLintableSql(input))
  const catalogueMetadata = useCatalogueMetadata()
  const catalogueStatus = useCatalogueStatus()
  const editorExtensions = useMemo(
    () => [
      workbenchPromptTheme,
      ...createCatalogueCompletionExtensions(catalogueMetadata.data),
      EditorView.lineWrapping,
    ],
    [catalogueMetadata.data]
  )

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ block: "nearest" })
  }, [transcript])

  function replaceInput(value: string) {
    setInput(value)
    requestAnimationFrame(() => {
      const editor = editorRef.current
      if (!editor) return
      editor.dispatch({ selection: { anchor: editor.state.doc.length } })
      editor.focus()
    })
  }

  function clearPrompt() {
    const editor = editorRef.current
    if (editor && editor.state.doc.length > 0) {
      editor.dispatch({
        changes: { from: 0, to: editor.state.doc.length, insert: "" },
        selection: { anchor: 0 },
      })
    }
    setInput("")
  }

  function navigateHistory(direction: "up" | "down") {
    if (history.length === 0) return

    if (direction === "up") {
      if (historyIndex === null) historyDraftRef.current = input
      const nextIndex =
        historyIndex === null
          ? history.length - 1
          : Math.max(0, historyIndex - 1)
      setHistoryIndex(nextIndex)
      replaceInput(history[nextIndex])
      return
    }

    if (historyIndex === null) return
    if (historyIndex < history.length - 1) {
      const nextIndex = historyIndex + 1
      setHistoryIndex(nextIndex)
      replaceInput(history[nextIndex])
      return
    }

    setHistoryIndex(null)
    replaceInput(historyDraftRef.current)
  }

  function canNavigateHistory(direction: "up" | "down") {
    const editor = editorRef.current
    if (!editor || !editor.state.selection.main.empty) return false

    const head = editor.state.selection.main.head
    const line = editor.state.doc.lineAt(head)
    return direction === "up"
      ? line.number === 1
      : historyIndex !== null && line.number === editor.state.doc.lines
  }

  function execute() {
    const sql = input.trim()
    if (!sql) return
    const command = parseWorkbenchCommand(sql)

    if (isClearCommand(sql)) {
      setTranscript([])
      clearPrompt()
      setHistoryIndex(null)
      historyDraftRef.current = ""
      return
    }

    if (command) {
      const id = crypto.randomUUID()
      const outcome = runWorkbenchCommand(command, {
        metadata: catalogueMetadata.data,
        status: catalogueStatus.data,
        history,
      })
      let entry: TranscriptEntry

      switch (outcome.kind) {
        case "help":
          entry = { id, sql, status: "success", output: "help" }
          break
        case "result":
          entry = {
            id,
            sql,
            status: "success",
            result: outcome.result,
            expanded: expandedOutput,
          }
          break
        case "message":
          entry = {
            id,
            sql,
            status: "success",
            output: "message",
            message: outcome.message,
          }
          break
        case "toggle-expanded": {
          const next = !expandedOutput
          setExpandedOutput(next)
          entry = {
            id,
            sql,
            status: "success",
            output: "message",
            message: `Expanded result display is ${next ? "on" : "off"}.`,
          }
          break
        }
        case "error":
          entry = { id, sql, status: "error", error: outcome.error }
          break
      }

      setTranscript((entries) => [...entries, entry])
      setHistory((entries) =>
        entries.at(-1) === sql ? entries : [...entries, sql]
      )
      clearPrompt()
      setHistoryIndex(null)
      historyDraftRef.current = ""
      return
    }

    if (isWorkbenchCommandLike(sql)) {
      const id = crypto.randomUUID()
      const suggestion = workbenchCommandSuggestion(sql)
      setTranscript((entries) => [
        ...entries,
        {
          id,
          sql,
          status: "error",
          error: suggestion
            ? `Unknown meta-command: ${sql}. Did you mean ${suggestion}?`
            : `Unknown meta-command: ${sql}. Try \\?.`,
        },
      ])
      setHistory((entries) =>
        entries.at(-1) === sql ? entries : [...entries, sql]
      )
      clearPrompt()
      setHistoryIndex(null)
      historyDraftRef.current = ""
      return
    }

    if (!isTerminatedSql(sql)) return
    if (catalogueQuery.isPending) return

    const id = crypto.randomUUID()
    const startedAt = performance.now()
    setTranscript((entries) => [
      ...entries,
      { id, sql, status: "running", expanded: expandedOutput },
    ])
    setHistory((entries) =>
      entries.at(-1) === sql ? entries : [...entries, sql]
    )
    clearPrompt()
    setHistoryIndex(null)
    historyDraftRef.current = ""

    catalogueQuery.mutate(
      { sql, mode: "run" },
      {
        onSuccess: (result) =>
          setTranscript((entries) =>
            entries.map((entry) =>
              entry.id === id
                ? {
                    ...entry,
                    status: "success",
                    result,
                    durationMs: performance.now() - startedAt,
                  }
                : entry
            )
          ),
        onError: (error) =>
          setTranscript((entries) =>
            entries.map((entry) =>
              entry.id === id
                ? {
                    ...entry,
                    status: "error",
                    error: extractApiError(error),
                    durationMs: performance.now() - startedAt,
                  }
                : entry
            )
          ),
      }
    )
  }

  return (
    <section
      aria-label="Catalogue SQL workbench"
      className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background text-foreground"
    >
      <div className="relative flex min-h-0 flex-1 flex-col overflow-auto bg-background">
        <div className="flex flex-1 flex-col px-5 py-6 sm:px-6">
          <div className="space-y-7">
            {transcript.map((entry) => (
              <div key={entry.id} className="font-mono">
                {entry.output !== "welcome" && (
                  <div className="flex min-w-0 items-start gap-2">
                    <PromptGutter value={entry.sql} />
                    <pre className="min-w-0 flex-1 py-1 text-[13px] leading-6 whitespace-pre-wrap text-foreground/85">
                      {entry.sql}
                      {entry.status === "running" && (
                        <DelayedRunningIndicator compact />
                      )}
                    </pre>
                    {!isMetaCommand(entry.sql) && (
                      <TranscriptActions
                        sql={entry.sql}
                        onSave={(kind, sql) => setSaveTarget({ kind, sql })}
                      />
                    )}
                  </div>
                )}
                <div className="pt-1">
                  {entry.status === "success" && entry.output === "welcome" && (
                    <WelcomeOutput
                      schemaVersion={
                        catalogueStatus.data?.catalogue_schema_version
                      }
                    />
                  )}
                  {entry.status === "error" && (
                    <p className="text-xs text-red-700 dark:text-red-300/80">
                      {entry.error}
                    </p>
                  )}
                  {entry.status === "success" && entry.output === "help" && (
                    <HelpOutput />
                  )}
                  {entry.status === "success" && entry.output === "message" && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      {entry.message}
                    </p>
                  )}
                  {entry.status === "success" && entry.result && (
                    <ResultTable
                      result={entry.result}
                      durationMs={entry.durationMs}
                      expanded={entry.expanded}
                    />
                  )}
                </div>
              </div>
            ))}

            <div className="font-mono">
              <div className="flex min-w-0 items-start gap-2">
                <PromptGutter value={input} />
                <div className="min-h-48 min-w-0 flex-1">
                  <CodeMirror
                    autoFocus
                    aria-label="SQL prompt"
                    value={input}
                    theme="none"
                    extensions={editorExtensions}
                    basicSetup={{
                      lineNumbers: false,
                      foldGutter: false,
                      dropCursor: false,
                      allowMultipleSelections: false,
                      indentOnInput: true,
                      bracketMatching: true,
                      closeBrackets: true,
                      autocompletion: false,
                      highlightSelectionMatches: false,
                      highlightActiveLine: false,
                      highlightActiveLineGutter: false,
                      syntaxHighlighting: false,
                      highlightSpecialChars: false,
                    }}
                    onCreateEditor={(view) => {
                      editorRef.current = view
                      view.dispatch({
                        selection: { anchor: view.state.doc.length },
                      })
                    }}
                    onChange={setInput}
                    onKeyDown={(event) => {
                      if (
                        !event.altKey &&
                        !event.ctrlKey &&
                        !event.metaKey &&
                        !event.shiftKey &&
                        event.key === "ArrowUp" &&
                        canNavigateHistory("up")
                      ) {
                        event.preventDefault()
                        navigateHistory("up")
                        return
                      }
                      if (
                        !event.altKey &&
                        !event.ctrlKey &&
                        !event.metaKey &&
                        !event.shiftKey &&
                        event.key === "ArrowDown" &&
                        canNavigateHistory("down")
                      ) {
                        event.preventDefault()
                        navigateHistory("down")
                        return
                      }
                      const selection = editorRef.current?.state.selection.main
                      const submitsAtCursor =
                        selection?.empty &&
                        selection.head === editorRef.current?.state.doc.length
                      if (
                        event.key === "Enter" &&
                        submitsAtCursor &&
                        runsOnEnter(input)
                      ) {
                        event.preventDefault()
                        event.nativeEvent.stopImmediatePropagation()
                        execute()
                      }
                    }}
                  />
                </div>
              </div>
            </div>
          </div>
          <div ref={transcriptEndRef} />
        </div>
      </div>

      <footer className="flex h-9 shrink-0 items-center justify-between gap-4 border-t bg-background px-4 font-mono text-[10px] text-muted-foreground">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <span className="hidden shrink-0 items-center gap-1.5 sm:flex">
            <LockKeyholeIcon className="size-3" />
            read only
          </span>
          {catalogueQuery.isPending ? (
            <DelayedRunningIndicator />
          ) : (
            <FooterLintDiagnostics
              diagnostics={catalogueLint.data?.diagnostics ?? []}
            />
          )}
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span
            className="hidden sm:inline"
            title={
              catalogueStatus.data
                ? `${catalogueStatus.data.active_file_count.toLocaleString()} active Parquet files`
                : undefined
            }
          >
            catalogue {formatBytes(catalogueStatus.data?.active_storage_bytes)}
          </span>
          <span className="hidden md:inline">
            DuckLake {catalogueStatus.data?.ducklake_version ?? "—"}
          </span>
        </div>
      </footer>

      <SaveQueryDialog
        open={saveTarget?.kind === "query"}
        onOpenChange={(open) => !open && setSaveTarget(null)}
        sql={saveTarget?.sql ?? ""}
        query={null}
        onSaved={() => setSaveTarget(null)}
      />
      <SaveViewDialog
        open={saveTarget?.kind === "view"}
        onOpenChange={(open) => !open && setSaveTarget(null)}
        sql={saveTarget?.sql ?? ""}
      />
      <SaveTableMacroDialog
        open={saveTarget?.kind === "macro"}
        onOpenChange={(open) => !open && setSaveTarget(null)}
        sql={saveTarget?.sql ?? ""}
      />
    </section>
  )
}

function formatBytes(value: number | undefined): string {
  if (value === undefined) return "—"
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}

function formatDuration(value: number | undefined): string {
  if (value === undefined) return "—"
  if (value < 1_000) return `${Math.round(value)} ms`
  return `${(value / 1_000).toFixed(2)} s`
}
