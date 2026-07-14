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
  output?: "help" | "welcome"
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
const TERMINAL_COMMANDS = [
  { command: "\\?", description: "Show available meta-commands" },
  { command: "\\dt", description: "List catalogue tables and views" },
  { command: "clear", description: "Clear the transcript" },
] as const
const LIST_TABLES_SQL = `
SELECT
  table_schema AS schema,
  table_name AS name,
  CASE table_type WHEN 'BASE TABLE' THEN 'table' ELSE lower(table_type) END AS kind
FROM information_schema.tables
WHERE table_catalog = 'atlas'
  AND table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY table_schema, table_name;
`

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
    ".cm-placeholder": { color: "var(--muted-foreground)" },
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

function isClearCommand(value: string) {
  return normalizeCommand(value) === "clear"
}

function terminalCommand(value: string): "\\?" | "\\dt" | null {
  const command = value.trim().toLowerCase()
  return command === "\\?" || command === "\\dt" ? command : null
}

function isCommandLike(value: string) {
  const input = value.trimStart()
  return input.startsWith("\\") || input.startsWith("/")
}

function commandSuggestion(value: string): "\\?" | "\\dt" | null {
  const command = value.trim().toLowerCase()
  if (command === "/?") return "\\?"
  if (command === "/dt") return "\\dt"
  return null
}

function normalizeCommand(value: string) {
  return value.trim().replace(/;$/, "").trim().toLowerCase()
}

function isTerminatedSql(value: string) {
  return value.trimEnd().endsWith(";")
}

function isLintableSql(value: string) {
  return (
    Boolean(value.trim()) && !isClearCommand(value) && !isCommandLike(value)
  )
}

const MIN_COLUMN_WIDTH = 120
const INITIAL_COLUMN_WIDTH = 180

function HelpOutput() {
  return (
    <dl className="mt-2 space-y-1 font-mono text-xs">
      {TERMINAL_COMMANDS.map(({ command, description }) => (
        <div key={command} className="flex gap-4">
          <dt className="w-14 shrink-0 text-primary">{command}</dt>
          <dd className="text-muted-foreground">{description}</dd>
        </div>
      ))}
    </dl>
  )
}

function WelcomeOutput() {
  return (
    <div className="space-y-1 font-mono text-xs">
      <p className="text-foreground/80">Atlas catalogue ready.</p>
      <p className="text-muted-foreground">
        End SQL with <span className="text-foreground/70">;</span> to run ·{" "}
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
          className="h-6 border-transparent bg-transparent px-2 font-sans text-[10px] hover:bg-muted/60 dark:bg-transparent dark:hover:bg-muted/40"
        >
          <SaveIcon />
          <span>Save</span>
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
        className="h-6 px-2 font-sans text-[10px]"
        onClick={() => void copy()}
      >
        <CopyIcon />
        Copy
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

function ResultTable({
  result,
  durationMs,
}: {
  result: CatalogueQueryResult
  durationMs?: number
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

  return (
    <div className="mt-3 max-w-full">
      <div
        ref={containerRef}
        className="max-h-[min(42vh,24rem)] w-full overflow-auto"
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
                    onKeyDown={(event) => {
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
    const command = terminalCommand(sql)

    if (isClearCommand(sql)) {
      setTranscript([])
      clearPrompt()
      setHistoryIndex(null)
      historyDraftRef.current = ""
      return
    }

    if (command === "\\?") {
      const id = crypto.randomUUID()
      setTranscript((entries) => [
        ...entries,
        { id, sql, status: "success", output: "help" },
      ])
      setHistory((entries) =>
        entries.at(-1) === sql ? entries : [...entries, sql]
      )
      clearPrompt()
      setHistoryIndex(null)
      historyDraftRef.current = ""
      return
    }

    if (isCommandLike(sql) && command === null) {
      const id = crypto.randomUUID()
      const suggestion = commandSuggestion(sql)
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

    if (!isTerminatedSql(sql) && command !== "\\dt") return
    if (catalogueQuery.isPending) return

    const id = crypto.randomUUID()
    const startedAt = performance.now()
    setTranscript((entries) => [...entries, { id, sql, status: "running" }])
    setHistory((entries) =>
      entries.at(-1) === sql ? entries : [...entries, sql]
    )
    clearPrompt()
    setHistoryIndex(null)
    historyDraftRef.current = ""

    catalogueQuery.mutate(
      { sql: command === "\\dt" ? LIST_TABLES_SQL : sql, mode: "run" },
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
                    <TranscriptActions
                      sql={entry.sql}
                      onSave={(kind, sql) => setSaveTarget({ kind, sql })}
                    />
                  </div>
                )}
                <div className="pt-1">
                  {entry.status === "success" && entry.output === "welcome" && (
                    <WelcomeOutput />
                  )}
                  {entry.status === "error" && (
                    <p className="text-xs text-red-700 dark:text-red-300/80">
                      {entry.error}
                    </p>
                  )}
                  {entry.status === "success" && entry.output === "help" && (
                    <HelpOutput />
                  )}
                  {entry.status === "success" && entry.result && (
                    <ResultTable
                      result={entry.result}
                      durationMs={entry.durationMs}
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
                    placeholder="Start typing SQL…"
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
                      if (
                        event.key === "Enter" &&
                        (isClearCommand(input) ||
                          isCommandLike(input) ||
                          isTerminatedSql(input))
                      ) {
                        event.preventDefault()
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
          <span>API {formatDuration(catalogueStatus.data?.apiLatencyMs)}</span>
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
