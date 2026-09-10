"use client"

import { readAssistantStream } from "@/lib/sql-assistant-stream"
import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"
import { extractApiError, responseJson } from "@/lib/api"
import { usePublicAccess } from "@/hooks/use-public-access"
import { sameSqlDraft, sqlAssistantInputSchema, type SqlAssistantInput, type SqlAssistantActivity, type SqlDraft } from "@/types/sql-assistant"

export function useSqlAssistant(draft: SqlDraft, selection: string, failure: SqlAssistantInput["failure"], onChange: (draft: SqlDraft) => void, queryMode: "stable" | "experimental" = "stable") {
  const access = usePublicAccess("assistant")
  const [activities, setActivities] = useState<SqlAssistantActivity[]>([])
  const [open, setOpen] = useState(false)
  const [intent, setIntent] = useState("")
  const [history, setHistory] = useState<SqlAssistantInput["history"]>([])
  const [undo, setUndo] = useState<{ before: SqlDraft; after: SqlDraft } | null>(null)
  const mutation = useMutation({
    mutationFn: async (input: SqlAssistantInput) => {
      if (!access.enabled) throw new Error(access.message ?? "The SQL assistant is unavailable.")
      const body = sqlAssistantInputSchema.parse(input)
      const response = await fetch("/api/sql-assistant", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
      })
      if (!response.ok) await responseJson(response)
      return readAssistantStream(response, event => {
        if (event.type !== "activity") return
        setActivities(current => [...current.filter(item => item.id !== event.activity.id), event.activity])
      })
    },
    onMutate: () => { setActivities([]); setIntent("") },
    onSuccess: (reply, input) => {
      setHistory([...input.history, { role: "user" as const, content: input.intent }, { role: "assistant" as const, content: reply.message }].slice(-20))
      setIntent(current => current === input.intent ? "" : current)
    },
    onError: (error, input) => { setIntent(current => current || input.intent); access.onDenied(error); toast.error(extractApiError(error)) },
  })
  const stale = !!mutation.variables && (mutation.variables.queryMode !== queryMode || !sameSqlDraft(draft, mutation.variables.draft))
  function show(prompt?: string) {
    setOpen(true)
    if (prompt) setIntent(prompt)
  }
  function submit() {
    if (!intent.trim() || mutation.isPending) return
    mutation.mutate({ queryMode, intent: intent.trim(), draft: { ...draft }, proposal: !stale && mutation.data?.sql ? { sql: mutation.data.sql, parameters: mutation.data.parameters } : null, selection, failure, history })
  }
  function apply() {
    if (!mutation.data?.sql || !mutation.variables || stale || mutation.isPending || mutation.data.validation?.status === "invalid") return
    const next = { sql: mutation.data.sql, parameters: mutation.data.parameters }
    setUndo({ before: { ...draft }, after: next })
    onChange(next)
    mutation.reset()
  }
  function undoApply() {
    if (!undo || !sameSqlDraft(draft, undo.after)) return
    onChange(undo.before)
    setUndo(null)
  }
  return {
    ...mutation, activities, history, open, setOpen, show, intent, setIntent, submit, apply, stale, access,
    dismiss: () => mutation.reset(),
    canUndo: !!undo && sameSqlDraft(draft, undo.after), undoApply,
  }
}
