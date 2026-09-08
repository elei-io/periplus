"use client"

import { ArrowUp, MessageSquare, Undo2, X } from "lucide-react"
import { useEffect, useRef } from "react"
import type { useSqlAssistant } from "@/hooks/use-sql-assistant"
import { sqlDiff } from "@/lib/sql-diff"
import { extractApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { CardDescription, CardTitle } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { SqlExample } from "@/components/sql-example"

export function SqlAssistant({ assistant }: { assistant: ReturnType<typeof useSqlAssistant> }) {
  const inputRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { if (assistant.open) inputRef.current?.focus() }, [assistant.open])
  const reply = assistant.data
  const before = assistant.variables?.draft
  return <div id="sql-assistant" hidden={!assistant.open}>
    <Separator />
    <section aria-label="SQL assistant" className="flex flex-col gap-3 p-3">
      <div className="flex items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2"><MessageSquare className="size-4" />Ask SQL</CardTitle>
        <div className="flex items-center gap-2">
          {assistant.canUndo && <Button variant="ghost" size="sm" onClick={assistant.undoApply}><Undo2 />Undo apply</Button>}
          <Button variant="ghost" size="icon-sm" aria-label="Close SQL assistant" onClick={() => assistant.setOpen(false)}><X /></Button>
        </div>
      </div>
      <CardDescription>Describe the rows you want, or ask for a change. Review the SQL before applying it.</CardDescription>
      {reply && !assistant.isPending && <div className="flex flex-col gap-3" aria-live="polite">
        <p>{reply.message}</p>
        {reply.sql !== null && <>
          {before?.sql.trim() ? <details open><summary>Proposed changes · − removed / + added</summary><pre aria-label="Proposed SQL changes" className="max-h-64 overflow-auto py-3">{sqlDiff(before.sql, reply.sql)}</pre></details> : <SqlExample sql={reply.sql} />}
          {before?.parameters !== reply.parameters && <details open><summary>Parameter changes</summary><pre className="max-h-40 overflow-auto py-3">{`Before: ${before?.parameters}\nAfter:  ${reply.parameters}`}</pre></details>}
          {reply.validation && <Alert variant={reply.validation.status === "prepared" ? "default" : "destructive"}><AlertDescription>{reply.validation.message}</AlertDescription></Alert>}
          {assistant.stale && <CardDescription>The editor changed since this suggestion. Ask again to update it.</CardDescription>}
          <div className="flex gap-2"><Button size="sm" onClick={assistant.apply} disabled={assistant.stale || reply.validation?.status === "invalid"}>Apply</Button><Button size="sm" variant="ghost" onClick={assistant.dismiss}>Dismiss</Button></div>
        </>}
      </div>}
      {assistant.error && <Alert variant="destructive"><AlertDescription>{extractApiError(assistant.error)}</AlertDescription></Alert>}
      <form className="flex items-end gap-2" onSubmit={event => { event.preventDefault(); assistant.submit() }}>
        <Textarea ref={inputRef} aria-label="What should this query return?" placeholder={reply ? "Ask a follow-up or describe another change…" : "What should this query return?"} value={assistant.intent} maxLength={4_000} onChange={event => assistant.setIntent(event.target.value)} />
        <Button type="submit" size="icon" aria-label="Send SQL request" disabled={assistant.isPending || !assistant.intent.trim() || !assistant.access.enabled}><ArrowUp /></Button>
      </form>
      <CardDescription role="status">{assistant.isPending ? "Drafting SQL and checking it with Periplus…" : assistant.access.message ?? "Uses your SQL, selected text, parameters and latest error. Apply does not run the query."}</CardDescription>
    </section>
  </div>
}
