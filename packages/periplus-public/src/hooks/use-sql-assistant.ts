"use client"

import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"
import { extractApiError, responseJson } from "@/lib/api"
import { usePublicAccess } from "@/hooks/use-public-access"
import { sameSqlDraft, sqlAssistantInputSchema, type SqlAssistantInput, type SqlAssistantReply, type SqlDraft } from "@/types/sql-assistant"

export function useSqlAssistant(draft: SqlDraft, selection: string, failure: SqlAssistantInput["failure"], onChange: (draft: SqlDraft) => void) {
  const access = usePublicAccess("assistant")
  const [open, setOpen] = useState(false)
  const [intent, setIntent] = useState("")
  const [history, setHistory] = useState<SqlAssistantInput["history"]>([])
  const [undo, setUndo] = useState<{ before: SqlDraft; after: SqlDraft } | null>(null)
  const mutation = useMutation({
    mutationFn: async (input: SqlAssistantInput) => {
      if (!access.enabled) throw new Error(access.message ?? "The SQL assistant is unavailable.")
      const body = sqlAssistantInputSchema.parse(input)
      return responseJson<SqlAssistantReply>(await fetch("/api/sql-assistant", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
      }))
    },
    onSuccess: (reply, input) => {
      setHistory([...input.history, { role: "user" as const, content: input.intent }, { role: "assistant" as const, content: reply.message }].slice(-6))
      setIntent(current => current === input.intent ? "" : current)
    },
    onError: error => { access.onDenied(error); toast.error(extractApiError(error)) },
  })
  const stale = !!mutation.variables && !sameSqlDraft(draft, mutation.variables.draft)
  function show(prompt?: string) {
    setOpen(true)
    if (prompt) setIntent(prompt)
  }
  function submit() {
    if (!intent.trim() || mutation.isPending) return
    mutation.mutate({ intent: intent.trim(), draft: { ...draft }, proposal: !stale && mutation.data?.sql ? { sql: mutation.data.sql, parameters: mutation.data.parameters } : null, selection, failure, history })
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
    ...mutation, open, setOpen, show, intent, setIntent, submit, apply, stale, access,
    dismiss: () => mutation.reset(),
    canUndo: !!undo && sameSqlDraft(draft, undo.after), undoApply,
  }
}
