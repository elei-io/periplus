"use client"

import Link from "next/link"
import { Check, ChevronRight, LoaderCircle, CircleAlert } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { memo, useMemo, useState } from "react"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Table, TableHeader, TableHead, TableBody, TableRow, TableCell } from "@/components/ui/table"
import { analysisView } from "@/lib/analysis-view"
import type { DiscoveryMessage } from "@/types/assistant"

export function ChatActivity({ message, running = false }: { message?: DiscoveryMessage; running?: boolean }) {
  const queries = message ? analysisView(message).queries : []
  const [open, setOpen] = useState(false)
  const latest = queries.at(-1)
  if (!running && !latest) return null
  const pending = latest && (latest.state === "input-streaming" || latest.state === "input-available")
  const failed = latest && (latest.state === "output-error" || (latest.state === "output-available" && latest.output.error))
  const label = pending ? running ? `Executing SQL · ${latest.input?.purpose ?? "Query"}` : "SQL query interrupted"
    : failed ? `SQL failed · ${latest.input?.purpose ?? "Query"}`
    : latest ? `Completed · ${latest.input?.purpose ?? "SQL query"}` : "Thinking…"
  const Icon = running && (!latest || pending) ? LoaderCircle : failed || pending ? CircleAlert : Check
  return <div className="chat-activity">
    <Button type="button" variant="ghost" size="sm" className="chat-activity-toggle" aria-label={label} aria-expanded={open} disabled={!queries.length} onClick={() => setOpen(!open)}>
      <Icon className={running && (!latest || pending) ? "animate-spin" : undefined} />
      <span role="status">{label}</span>
      {queries.length > 0 && <ChevronRight className={open ? "rotate-90" : undefined} />}
    </Button>
    {open && <div className="chat-query-list">{queries.map(part => <div key={part.toolCallId}>
      <p>{part.input?.purpose ?? "SQL query"}</p>
      {part.input?.sql && <pre tabIndex={0}><code>{part.input.sql}</code></pre>}
      {part.state === "output-available" && <p>{part.output.error ?? `${part.output.result?.rows.length ?? 0} rows returned`}</p>}
      {part.state === "output-error" && <p>{part.errorText}</p>}
    </div>)}</div>}
  </div>
}

export const AnalysisAnswer = memo(function AnalysisAnswer({ message, running = false, showCoverage = true }: { message: DiscoveryMessage; running?: boolean; showCoverage?: boolean }) {
  const { finding, coverage } = useMemo(() => analysisView(message), [message])
  return <div className="flex flex-col gap-3">
    <ChatActivity message={message} running={running} />
    {finding && <div className="conversation-prose" aria-live="polite"><Markdown remarkPlugins={[remarkGfm]} skipHtml allowedElements={["p", "strong", "em", "ul", "ol", "li", "a", "h1", "h2", "h3", "code", "pre", "table", "thead", "tbody", "tr", "th", "td"]} components={{ table: ({ children }) => <Table>{children}</Table>, thead: ({ children }) => <TableHeader>{children}</TableHeader>, tbody: ({ children }) => <TableBody>{children}</TableBody>, tr: ({ children }) => <TableRow>{children}</TableRow>, th: ({ children }) => <TableHead>{children}</TableHead>, td: ({ children }) => <TableCell>{children}</TableCell> }} unwrapDisallowed>{finding}</Markdown></div>}
    {showCoverage && coverage.map((suggestion, index) => <Card key={index} size="sm"><CardHeader><CardTitle>Suggested coverage request</CardTitle></CardHeader><CardContent className="flex flex-col gap-2"><p>{suggestion.reason}</p><p>{suggestion.description}</p><Button variant="outline" nativeButton={false} render={<Link href={`/coverage?${new URLSearchParams({ description: suggestion.description })}#coverage-request`} target="_blank" rel="noopener noreferrer" />}>Review coverage request</Button></CardContent></Card>)}

  </div>
})
