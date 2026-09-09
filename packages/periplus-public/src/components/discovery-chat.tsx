"use client"

import Link from "next/link"
import { usePublicAccess } from "@/hooks/use-public-access"
import { responseJson } from "@/lib/api"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { consumeDiscoveryLaunch } from "@/lib/discovery-launch"
import { useChat } from "@ai-sdk/react"
import { DefaultChatTransport } from "ai"
import { ArrowUp, Square, LoaderCircle, Plus, Columns3 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { AnalysisAnswer } from "@/components/analysis-answer"
import { DatasetSpecification } from "@/components/dataset-specification"
import { analysisHistory, analysisView } from "@/lib/analysis-view"
import { extractApiError } from "@/lib/api"
import type { DiscoveryMessage } from "@/types/assistant"
import posthog from "posthog-js"

const starters = [
  { label: "Source directory", text: "List websites in Periplus, with one row per hostname, distinct page counts, and first and last observation dates. Show a small sample first." },
  { label: "Page headings", text: "Make a heading index from up to 10 collected pages, using the latest dated observation per URL. Include heading level, full heading text, source URL, and observation date. Show a small sample first." },
  { label: "Website relationships", text: "Show relationships between websites: one row per source and destination hostname, with a count of distinct linking pages. Show a small sample first." },
]

function latestPresentation(messages: DiscoveryMessage[]) {
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index]
    if (message.role !== "assistant") continue
    const presentation = analysisView(message).presentation
    if (presentation) return { id: message.id, presentation }
  }
}

export function DiscoveryChat({ initialPrompt, autoRun = false }: { initialPrompt?: string; autoRun?: boolean }) {
  const composer = useRef<HTMLTextAreaElement>(null)
  const access = usePublicAccess("assistant")
  const { onDenied } = access
  const transport = useMemo(() => new DefaultChatTransport<DiscoveryMessage>({ api:"/api/assistant",
    prepareSendMessagesRequest:({messages, body}) => ({body:{messages:analysisHistory(messages), approval: body?.approval}}),
    fetch: async (input, init) => {
      const response = await fetch(input, init)
      if (!response.ok) { try { await responseJson(response.clone()) } catch(error) { onDenied(error); throw error } }
      return response
    },
  }), [onDenied])
  const [prompt, setPrompt] = useState(autoRun ? "" : initialPrompt ?? "")
  const { messages, sendMessage, status, stop, error, setMessages } = useChat<DiscoveryMessage>({ transport, onError: error => toast.error(extractApiError(error)) })
  useEffect(() => {
    if (access.enabled && autoRun && initialPrompt?.trim() && consumeDiscoveryLaunch()) void sendMessage({ text: initialPrompt })
  }, [autoRun, initialPrompt, sendMessage, access.enabled])
  const busy = status === "submitted" || status === "streaming"
  const send = useCallback((text: string, approval?: string) => {
    if (!access.enabled || !text.trim() || busy) return
    if (text.length > 7800) { toast.error("The definition is too long. Shorten field descriptions before building."); return }
    if (messages.length === 0 && !approval) {
      posthog.capture("discovery_dataset_started", { prompt_length: text.length })
    }
    void sendMessage({ text }, { body: { approval } }); setPrompt("")
  }, [access.enabled, busy, messages.length, sendMessage])
  const latest = useMemo(() => latestPresentation(messages), [messages])
  const presentation = latest?.presentation
  const approve = useCallback((token: string) => send("Build the full dataset using these sources and fields.", token), [send])
  const edit = () => { setPrompt("I'd like to change "); composer.current?.focus() }
  return <section className="dataset-discovery-layout grid items-start lg:grid-cols-12" aria-label="Dataset discovery">
    <div className="discovery-conversation flex min-w-0 flex-col gap-6 lg:col-span-8">
      <div className="discovery-conversation-heading flex items-center justify-between gap-4">{messages.length > 0 && <h2>Conversation</h2>}{access.message && <p role="status">{access.message} {access.data?.sql.enabled && <Link href="/sql">Open SQL</Link>}</p>}{messages.length > 0 && <Button variant="outline" size="sm" disabled={busy || !access.enabled} onClick={() => { setMessages([]); setPrompt("") }}><Plus />New dataset</Button>}</div>

      {messages.map((message, index) => {
        if (message.role === "user") {
          const text = message.parts.flatMap(part => part.type === "text" ? [part.text] : []).join("\n")
          return <Card key={message.id} className="discovery-user-message"><CardHeader><CardTitle>You</CardTitle></CardHeader><CardContent><p className="whitespace-pre-wrap">{text}</p></CardContent></Card>
        }
        return <section key={message.id} className="flex min-w-0 flex-col gap-4" aria-label="Periplus response">
          <strong>Periplus</strong>
          <AnalysisAnswer message={message} running={busy && index === messages.length - 1} current={message.id === latest?.id} busy={busy || !access.enabled || message.id !== messages.at(-1)?.id} onApprove={approve} />
        </section>
      })}
      {busy && <p role="status" className="flex items-center gap-2"><LoaderCircle className="animate-spin" />{messages.at(-1)?.role === "assistant" ? "Working…" : "Getting started…"}</p>}
      {error && <p role="alert">{extractApiError(error)} Your definition remains available.</p>}
    <form className="discovery-composer" onSubmit={event => { event.preventDefault(); send(prompt) }}>
      <div className="composer-caption"><label htmlFor="dataset-idea">{messages.length ? "Message" : "Describe your dataset"}</label></div>
      <Textarea ref={composer} className="composer-input" id="dataset-idea" placeholder={messages.length ? "Change a field, explain a relationship, or resolve an open decision…" : "A dataset of book listings, with a title, price and link to each listing…"} value={prompt} maxLength={4000} onChange={event => setPrompt(event.target.value)} rows={3} />
      <div className="composer-actions"><span>Define the rows. We’ll explore the sources.</span>{busy ? <Button type="button" variant="outline" onClick={() => stop()}><Square />Stop</Button> : <Button type="submit" disabled={!access.enabled || !prompt.trim()}>{messages.length ? "Send" : "Start discovering"}<ArrowUp /></Button>}</div>
    </form>
      {!messages.length && <div className="discovery-starters"><span>A few places to start</span><div className="flex flex-wrap gap-2">{starters.map(({ label, text }) => <Button key={label} variant="ghost" size="sm" disabled={!access.enabled || busy} onClick={() => { setPrompt(text); composer.current?.focus(); posthog.capture("discovery_example_used", { example_label: label }) }}>{label}<ArrowUp /></Button>)}</div></div>}
      <details className="discovery-notes"><summary>About this workspace</summary><p>Uses data already in Periplus. <Link href="/coverage#coverage">Inspect coverage</Link> or <Link href="/coverage">request broader coverage</Link>.</p><p>The agent sees up to 20 rows per query; previews and CSV exports follow the configured query limits (1,000 rows by default). Definitions and sampled results go to the model provider. This workspace is temporary: download your definition and SQL before reloading. <Link href="/about#access">Access & data use</Link></p></details>
    </div>
    <aside aria-label="Your dataset definition" className="discovery-definition min-w-0 break-words lg:sticky lg:top-6 lg:col-span-4 lg:max-h-[calc(100dvh-3rem)] lg:overflow-y-auto">
      {presentation ? <DatasetSpecification draft={presentation.brief} busy={busy || !access.enabled} onEdit={edit} /> : <div className="discovery-definition-empty"><div className="discovery-definition-label"><Columns3 /><h2>Your dataset</h2><span>Draft</span></div><h3>Your definition starts here.</h3><p>Review your columns, sources, and selection rules here as you build.</p><dl><div><dt>Columns</dt><dd>The fields you need</dd></div><div><dt>Sources</dt><dd>The material behind each row</dd></div><div><dt>Scope</dt><dd>What belongs in your dataset</dd></div></dl></div>}
    </aside>
  </section>
}
