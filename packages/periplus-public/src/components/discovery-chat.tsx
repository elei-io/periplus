"use client"

import Link from "next/link"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useChat } from "@ai-sdk/react"
import { DefaultChatTransport } from "ai"
import { ArrowUp, Square, Plus } from "lucide-react"
import { toast } from "sonner"
import { usePublicAccess } from "@/hooks/use-public-access"
import { extractApiError, responseJson } from "@/lib/api"
import { consumeDiscoveryLaunch } from "@/lib/discovery-launch"
import { buildLink, discoverLink } from "@/lib/workspace-links"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Badge } from "@/components/ui/badge"
import { AnalysisAnswer, ChatActivity } from "@/components/analysis-answer"
import { DiscoveryFindings } from "@/components/discovery-findings"
import { DatasetResult } from "@/components/dataset-result"
import { DatasetSpecification, emptyDatasetBrief } from "@/components/dataset-specification"
import { workspaceOutput } from "@/lib/workspace-analytics"
import { analysisHistory, analysisView } from "@/lib/analysis-view"
import { datasetBriefSchema, datasetRequestSchema, sameDatasetBrief, type DatasetBrief, type DatasetMode } from "@/types/answer"
import type { DiscoveryMessage } from "@/types/assistant"
import { captureAnalytics, startAnalyticsOperation, finishAnalyticsOperation, analyticsErrorCategory } from "@/lib/analytics"
import type { AnalyticsOperation } from "@/types/analytics"

const starters = [
  { label: "Available websites", text: "What websites are available in Periplus? Show a few examples and what kinds of questions their data might help answer." },
  { label: "Explore available data", text: "Show a few examples of the data available in Periplus and the kinds of questions it could help answer." },
  { label: "Links between sites", text: "Can I explore how websites link to each other? Show a few real examples from the collected data." },
]

export function DiscoveryChat({ initialPrompt, initialDraft, autoRun = false, mode }: { initialPrompt?: string; initialDraft?: DatasetBrief; autoRun?: boolean; mode: DatasetMode }) {
  const [interrupted, setInterrupted] = useState(false)
  const [generation, setGeneration] = useState(0)
  const [panel, setPanel] = useState("schema")
  const [editedContract, setEditedContract] = useState<DatasetBrief | undefined>(initialDraft)
  const operation = useRef<AnalyticsOperation | null>(null)
  const evidenceOperation = useRef<string | null>(null)
  const validating = useRef(false)
  const composer = useRef<HTMLTextAreaElement>(null)
  const access = usePublicAccess("assistant")
  const { onDenied } = access
  const transport = useMemo(() => new DefaultChatTransport<DiscoveryMessage>({ api: "/api/assistant",
    prepareSendMessagesRequest: ({ messages, body }) => ({ headers: { "x-periplus-operation-id": typeof body?.operation_id === "string" ? body.operation_id : "" }, body: { messages: analysisHistory(messages), mode, contract: body?.contract } }),
    fetch: async (input, init) => {
      const response = await fetch(input, init)
      if (!response.ok) { try { await responseJson(response.clone()) } catch (error) { onDenied(error); throw error } }
      return response
    },
  }), [mode, onDenied])
  const [prompt, setPrompt] = useState(autoRun ? "" : initialPrompt ?? "")
  const { messages, sendMessage, status, stop, error, setMessages } = useChat<DiscoveryMessage>({ transport, messages: [], onError: error => {
    if (operation.current && !operation.current.finished) {
      finishAnalyticsOperation(operation.current, "workspace_turn_finished", { workspace: mode, outcome: "failed", validation_requested: validating.current, error_category: analyticsErrorCategory(error) })
      if (validating.current) captureAnalytics("dataset_validation_finished", { workspace: mode, operation_id: operation.current.id, outcome: "failed" })
    }
    toast.error(extractApiError(error))
  }, onFinish: ({ message, isAbort, isError, isDisconnect }) => {
    setInterrupted(Boolean(isAbort || isError || isDisconnect))
    if (!operation.current || operation.current.finished) return
    const output = workspaceOutput(message)
    const outcome = isAbort ? "cancelled" : isError || isDisconnect ? "failed" : "completed"
    const metadata = message.metadata
    finishAnalyticsOperation(operation.current, "workspace_turn_finished", {
      workspace: mode, outcome, ...output, validation_requested: validating.current,
      result_id: message.id, model: metadata?.model,
      input_tokens: metadata?.input_tokens, output_tokens: metadata?.output_tokens,
    })
    if (validating.current) captureAnalytics("dataset_validation_finished", {
      workspace: mode, operation_id: operation.current.id,
      outcome: outcome !== "completed" ? outcome : output.dataset_status === "ready" ? "validated" : output.dataset_status === "none" ? "no_result" : "needs_changes",
      issue_count: output.issue_count, row_count: output.row_count, truncated: output.truncated,
    })
  } })
  useEffect(() => {
    if (access.enabled && autoRun && initialPrompt?.trim() && consumeDiscoveryLaunch()) {
      operation.current = startAnalyticsOperation("workspace_turn_started", { workspace: mode, entry: "landing", validation_requested: false })
      void sendMessage({ text: initialPrompt }, { body: { operation_id: operation.current.id } })
    }
  }, [autoRun, initialPrompt, sendMessage, access.enabled, mode])
  useEffect(() => {
    const current = operation.current
    const message = messages.at(-1)
    if (!current || !message || message.role !== "assistant" || message.metadata?.operation_id !== current.id || evidenceOperation.current === current.id) return
    const output = workspaceOutput(message)
    if (!output.has_evidence) return
    evidenceOperation.current = current.id
    captureAnalytics("workspace_evidence_shown", { workspace: mode, operation_id: current.id, has_nonempty_evidence: output.has_nonempty_evidence, time_to_first_evidence_ms: Math.round(performance.now() - current.started) })
  }, [messages, mode])
  const busy = status === "submitted" || status === "streaming"
  const findingsMessage = [...messages].reverse().find(message => message.role === "assistant")
  const schemaMessage = [...messages].reverse().find(message => message.role === "assistant" && analysisView(message).schema)
  const proposedSchema = schemaMessage ? analysisView(schemaMessage).schema : undefined
  const latestMessage = [...messages].reverse().find(message => message.role === "assistant" && analysisView(message).presentation)
  const resultMessage = [...messages].reverse().find(message => message.role === "assistant" && analysisView(message).presentation?.dataset?.rows.length) ?? latestMessage
  const contract = editedContract ?? (mode === "build" ? proposedSchema : undefined)
  const presented = resultMessage ? analysisView(resultMessage).presentation : undefined
  const validated = presented?.status === "ready" && sameDatasetBrief(contract, presented.brief)
  const send = useCallback((text: string, requiredContract?: DatasetBrief) => {
    if (!access.enabled || !text.trim() || busy) return
    if (text.length > 7800) { toast.error("Shorten your message to 7,800 characters."); return }
    const parsed = datasetRequestSchema.safeParse({ mode, contract: requiredContract })
    if (!parsed.success) { toast.error("Complete the schema, row definition and source scope. Column names must be unique."); return }
    setInterrupted(false)
    if (mode === "build") setPanel(requiredContract ? "preview" : "schema")
    validating.current = Boolean(requiredContract)
    operation.current = startAnalyticsOperation("workspace_turn_started", { workspace: mode, validation_requested: Boolean(requiredContract), entry: initialPrompt || initialDraft ? "contextual" : "workspace" })
    void sendMessage({ text }, { body: { contract: requiredContract, operation_id: operation.current.id } }); setPrompt("")
  }, [access.enabled, busy, sendMessage, mode, initialPrompt, initialDraft])
  const suggestedBuildLink = presented ? buildLink(`Build this proposed dataset. Use this SQL as a starting point and verify it against the specification.\n\n${presented.dataset?.sql ?? ""}`, presented.brief) : "/build"
  return <section className="discovery-panels grid items-start gap-6 pb-8 lg:grid-cols-12" aria-label={mode === "discover" ? "Data discovery" : "Dataset builder"}>
    <div className="discovery-conversation flex min-w-0 flex-col gap-4 lg:col-span-5">
      <Card className="conversation-surface">
        <CardHeader><div className="flex items-center justify-between gap-2"><CardTitle>{mode === "discover" ? "Explore a question" : "Describe your requirements"}</CardTitle>{(messages.length > 0 || contract || mode === "build") && <Button variant="ghost" size="sm" disabled={busy} onClick={() => { setMessages([]); setGeneration(value => value + 1); setInterrupted(false); setEditedContract(undefined); setPanel("schema"); setPrompt("") }}><Plus />New</Button>}</div></CardHeader>
        <CardContent className="flex flex-col gap-4">
          {access.message && <p role="status">{access.message} {access.data?.sql.enabled && <Link href="/sql">Open SQL</Link>}</p>}
          {messages.length > 0 && <div className="conversation-scroll flex flex-col gap-5 overflow-y-auto break-words" aria-label="Conversation">{messages.map((message) => <div key={message.id} data-role={message.role} className="chat-message flex min-w-0 flex-col gap-2">{message.role === "user" ? <p className="whitespace-pre-wrap">{message.parts.flatMap(part => part.type === "text" ? [part.text] : []).join("\n")}</p> : <AnalysisAnswer message={message} running={busy && message.id === messages.at(-1)?.id} showCoverage={mode === "build"} />}</div>)}</div>}
          {busy && messages.at(-1)?.role !== "assistant" && <ChatActivity running />}
          {interrupted && <p role="status">The run stopped before completion. Completed query evidence remains available; you can continue from it.</p>}
          {error && <p role="alert">{extractApiError(error)}</p>}
          <form className="workspace-composer flex flex-col gap-3" onSubmit={event => { event.preventDefault(); send(mode === "build" && contract && !datasetBriefSchema.safeParse(contract).success ? `${prompt}\n\nUnfinished specification to refine:\n${JSON.stringify(contract)}` : prompt, mode === "build" && datasetBriefSchema.safeParse(contract).success ? contract : undefined) }}>
            <label htmlFor="dataset-idea">{mode === "discover" ? "What would you like to find out?" : "Your specification or next change"}</label>
            <Textarea ref={composer} id="dataset-idea" placeholder={mode === "discover" ? "Ask about the data…" : "Describe your dataset…"} value={prompt} maxLength={7800} onChange={event => setPrompt(event.target.value)} onKeyDown={event => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
                event.preventDefault()
                if (!event.repeat) event.currentTarget.form?.requestSubmit()
              }
            }} rows={1} />
            <div className="flex justify-end">{busy ? <Button type="button" variant="outline" onClick={() => stop()}><Square />Stop</Button> : <Button type="submit" disabled={!access.enabled || !prompt.trim()}>{messages.length ? "Send" : mode === "discover" ? "Explore data" : "Find matching data"}<ArrowUp /></Button>}</div>
          </form>
          {!messages.length && mode === "discover" && <div className="discovery-starters flex flex-wrap items-start gap-2">{starters.map(({ label, text }) => <Button key={label} variant="ghost" size="sm" disabled={!access.enabled || busy} onClick={() => send(text)}>{label}<ArrowUp /></Button>)}</div>}
        </CardContent>
      </Card>
      {mode === "build" && contract && <Button variant="ghost" className="self-start" nativeButton={false} render={<Link href={discoverLink(`Explore available sources for this dataset specification. Help me understand feasible scope and alternatives.\n\n${JSON.stringify(contract, null, 2)}`)} target="_blank" rel="noopener noreferrer" />}>Explore data for this specification ↗</Button>}
      <details className="workspace-disclosure"><summary>About this workspace</summary><p>Uses data already in Periplus. Conversations and previews are temporary. Downloads contain displayed rows; query limits apply. The model receives your definition and sampled results. <Link href="/about#access">Access and data use</Link></p></details>
    </div>
    <div className="discovery-results flex min-w-0 flex-col gap-4 lg:col-span-7">
      {mode === "discover" && proposedSchema && schemaMessage && <details className="workspace-disclosure schema-disclosure"><summary>Suggested schema · {proposedSchema.title}</summary><p>{proposedSchema.grain}</p><ul>{proposedSchema.fields.map(field => <li key={field.name}>{field.name} · {field.type} · {field.nullable ? "nullable" : "required"}</li>)}</ul><p>{proposedSchema.population}</p><Button variant="outline" disabled={busy} nativeButton={false} render={<Link href={buildLink("Find data matching this suggested schema and validate it.", proposedSchema)} target="_blank" rel="noopener noreferrer" />}>Use schema in builder</Button></details>}
      {mode === "build" ? <Tabs value={panel} onValueChange={value => setPanel(String(value))}><TabsList aria-label="Dataset workspace"><TabsTrigger value="schema">Schema</TabsTrigger><TabsTrigger value="preview">Preview</TabsTrigger></TabsList><TabsContent value="schema"><Card className="builder-schema-surface"><CardHeader><div className="flex flex-wrap items-center justify-between gap-2"><CardTitle>Dataset schema</CardTitle><Badge variant="outline">{validated ? "Validated" : editedContract ? "Draft" : proposedSchema ? "Suggested" : "New dataset"}</Badge></div></CardHeader><CardContent className="flex flex-col gap-4">{editedContract && proposedSchema && !sameDatasetBrief(editedContract, proposedSchema) && <details><summary>Compare latest agent proposal</summary><pre className="overflow-auto">{JSON.stringify(proposedSchema, null, 2)}</pre><Button variant="outline" disabled={busy} onClick={() => setEditedContract(proposedSchema)}>Use this proposal</Button></details>}<DatasetSpecification key={generation} preview={resultMessage ? analysisView(resultMessage).presentation?.dataset : undefined} draft={contract ?? emptyDatasetBrief} busy={busy} onChange={setEditedContract} />{validated ? <Button className="self-start" onClick={() => setPanel("preview")}>View validated dataset<ArrowUp /></Button> : Boolean(contract?.fields.length) && <Button className="self-start" disabled={busy || !access.enabled} onClick={() => send("Build and validate the dataset against this exact contract. Show any violations without relaxing requirements.", contract)}>Validate dataset<ArrowUp /></Button>}</CardContent></Card></TabsContent><TabsContent value="preview"><DatasetResult message={resultMessage} mode={mode} contract={contract} busy={busy} previous={Boolean(resultMessage && resultMessage.id !== messages.at(-1)?.id)} buildHref={suggestedBuildLink} />{findingsMessage && <DiscoveryFindings messages={messages} busy={busy} interrupted={interrupted} />}</TabsContent></Tabs> : <>{resultMessage && <DatasetResult message={resultMessage} mode={mode} contract={contract} busy={busy} previous={Boolean(resultMessage && resultMessage.id !== findingsMessage?.id)} buildHref={suggestedBuildLink} />}<DiscoveryFindings messages={messages} busy={busy} interrupted={interrupted} /></>}
    </div>
  </section>
}
