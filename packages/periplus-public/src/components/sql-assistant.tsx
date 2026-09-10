"use client"

import { AssistantMessage } from "@/components/assistant-message"
import { Check, CircleAlert, ArrowUp, MessageSquare, Undo2, X } from "lucide-react"
import { useEffect, useRef } from "react"
import type { useSqlAssistant } from "@/hooks/use-sql-assistant"
import { sqlDiff } from "@/lib/sql-diff"
import { extractApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { Progress } from "@/components/ui/progress"
import { Card, CardDescription, CardTitle } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { SqlExample } from "@/components/sql-example"

export function SqlAssistant({ assistant }: { assistant: ReturnType<typeof useSqlAssistant> }) {
  const inputRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { if (assistant.open) inputRef.current?.focus() }, [assistant.open])
  const reply = assistant.data
  const before = assistant.variables?.draft
  const steps = assistant.activities.filter(item => item.id !== "thinking")
  const currentStep = steps.find(item => item.status === "running")
  const activityLabel = assistant.isPending
    ? currentStep?.label ?? assistant.activities.find(item => item.id === "thinking")?.label ?? "Connecting to Ask SQL"
    : `${steps.length} steps${steps.some(item => item.status === "error") ? " · includes errors" : ""}`
  return <aside id="sql-assistant" aria-label="Ask SQL chat" className="min-w-0 lg:col-span-2 xl:col-span-1 xl:col-start-3 xl:row-start-1 xl:sticky xl:top-6">
    <Card className="flex max-h-[85vh] flex-col gap-0 overflow-hidden py-0">
    <div className="relative">
      <Separator />
      {assistant.isPending && <Progress value={null} aria-label="Preparing SQL suggestion" className="query-progress absolute inset-x-0 top-0" />}
    </div>
    <section aria-label="SQL assistant" className="flex min-h-0 flex-col gap-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2"><MessageSquare className="size-4" />Ask SQL</CardTitle>
        <div className="flex items-center gap-2">
          {assistant.canUndo && <Button variant="ghost" size="sm" onClick={assistant.undoApply}><Undo2 />Undo apply</Button>}
          <Button variant="ghost" size="icon-sm" aria-label="Close SQL assistant" onClick={() => assistant.setOpen(false)}><X /></Button>
        </div>
      </div>
      <CardDescription>Explore the data. Ask for a query.</CardDescription>
      <div role="log" aria-label="Conversation" className="flex min-h-0 flex-col gap-4 overflow-y-auto break-words">
      {assistant.history.slice(0, reply && !assistant.isPending ? -1 : undefined).map((message, index) => <div key={index} className={message.role === "user" ? "ml-6 rounded-lg bg-muted p-3" : "mr-2 py-2"}><CardDescription>{message.role === "user" ? "You" : "Ask SQL"}</CardDescription>{message.role === "assistant" ? <AssistantMessage content={message.content} /> : <p className="whitespace-pre-wrap">{message.content}</p>}</div>)}
      {assistant.isPending && assistant.variables && <div className="ml-6 rounded-lg bg-muted p-3"><CardDescription>You</CardDescription><p className="whitespace-pre-wrap">{assistant.variables.intent}</p></div>}
      {(assistant.isPending || steps.length > 0) && <details className="py-2" aria-label="Assistant activity">
        <summary className="cursor-pointer">
          <span className="inline-flex items-center gap-2 align-middle">
            {assistant.isPending ? <Spinner className="size-3.5" /> : assistant.error ? <CircleAlert className="size-3.5" /> : <Check className="size-3.5" />}
            <CardDescription>{activityLabel}{assistant.isPending ? "…" : ""}</CardDescription>
          </span>
        </summary>
        <div className="flex flex-col gap-3 pl-4 pt-3">
        {steps.map(item => <div key={item.id} className="flex items-start gap-2">
          {item.status === "running" && assistant.isPending ? <Spinner className="mt-1 size-3.5 shrink-0" /> : item.status === "error" || item.status === "running" ? <CircleAlert className="mt-1 size-3.5 shrink-0" /> : <Check className="mt-1 size-3.5 shrink-0" />}
          <div className="min-w-0 flex-1">
            <CardDescription>{item.label}{item.elapsedMs !== undefined ? ` · ${(item.elapsedMs / 1000).toFixed(1)}s` : item.status === "running" && !assistant.isPending ? " · interrupted" : ""}</CardDescription>
            {item.detail && <CardDescription>{item.detail}</CardDescription>}
            {item.sql && <details><summary className="cursor-pointer">View SQL</summary><pre className="max-h-48 overflow-auto py-2">{item.sql}</pre></details>}
          </div>
        </div>)}
        {assistant.isPending && !assistant.activities.some(item => item.id !== "thinking" && item.status === "running") && <div role="status" className="flex items-center gap-2 py-2"><Spinner className="size-3.5" /><CardDescription>{assistant.activities.find(item => item.id === "thinking")?.label ?? "Connecting to Ask SQL"}…</CardDescription></div>}
        </div>
      </details>}
      {reply && !assistant.isPending && <div className="flex flex-col gap-3" aria-live="polite">
        <CardDescription>Ask SQL</CardDescription><AssistantMessage content={reply.message} />
        {reply.sql !== null && <>
          {before?.sql.trim() ? <details open><summary>Proposed changes · − removed / + added</summary><pre aria-label="Proposed SQL changes" className="max-h-64 overflow-auto py-3">{sqlDiff(before.sql, reply.sql)}</pre></details> : <SqlExample sql={reply.sql} />}
          {before?.parameters !== reply.parameters && <details open><summary>Parameter changes</summary><pre className="max-h-40 overflow-auto py-3">{`Before: ${before?.parameters}\nAfter:  ${reply.parameters}`}</pre></details>}
          {reply.validation && <Alert variant={reply.validation.status === "prepared" ? "default" : "destructive"}><AlertDescription>{reply.validation.message}</AlertDescription></Alert>}
          {assistant.stale && <CardDescription>The editor changed since this suggestion. Ask again to update it.</CardDescription>}
          <div className="flex gap-2"><Button size="sm" onClick={assistant.apply} disabled={assistant.stale || reply.validation?.status === "invalid"}>Apply</Button><Button size="sm" variant="ghost" onClick={assistant.dismiss}>Dismiss</Button></div>
        </>}
      </div>}
      {assistant.error && <Alert variant="destructive"><AlertDescription>{extractApiError(assistant.error)}</AlertDescription></Alert>}
      </div>
      <form className="flex shrink-0 items-end gap-2 pt-2" onSubmit={event => { event.preventDefault(); assistant.submit() }}>
        <Textarea rows={1} className="min-h-9 max-h-40 overflow-y-auto" ref={inputRef} onKeyDown={event => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
            event.preventDefault()
            if (assistant.access.enabled) assistant.submit()
          }
        }} aria-label="Ask about the corpus" placeholder={reply ? "Ask a follow-up…" : "Ask about the corpus"} value={assistant.intent} maxLength={4_000} onChange={event => assistant.setIntent(event.target.value)} />
        <Button type="submit" size="icon" title="Send (Enter)" aria-label={assistant.isPending ? "Preparing SQL suggestion" : "Send SQL request"} disabled={assistant.isPending || !assistant.intent.trim() || !assistant.access.enabled}>{assistant.isPending ? <Spinner aria-hidden="true" /> : <ArrowUp />}</Button>
      </form>
      {assistant.access.message && <CardDescription>{assistant.access.message}</CardDescription>}
    </section>
    </Card>
  </aside>
}
