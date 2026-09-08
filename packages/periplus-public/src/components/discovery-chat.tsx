"use client"

import Link from "next/link"
import { usePublicAccess } from "@/hooks/use-public-access"
import { responseJson } from "@/lib/api"
import { useEffect, useState } from "react"
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
import type { DatasetBrief } from "@/types/answer"
import type { DiscoveryMessage } from "@/types/assistant"

const starters = [
  { label: "Source directory", text: "Help me define a dataset with one row per observed website, hostname, number of distinct observed URLs, and first and last observation dates. Use all current public observations. I want to understand the sources available for later dataset building. Start by proposing the definition." },
  { label: "Page headings", text: "Help me define a dataset of headings from a sample of up to 10 collected HTML pages, using the latest dated observation per URL. One row per heading, with source URL, observation date, heading level, and full nested heading text. I want a reusable text index. Start by proposing the definition." },
  { label: "Website relationships", text: "Help me define a dataset of source-to-destination website relationships from collected links. One row per distinct source hostname and target hostname, with the number of distinct linking source URLs. I want a network edge table. Start by proposing the definition." },
]

export function DiscoveryChat({ initialPrompt, autoRun = false }: { initialPrompt?: string; autoRun?: boolean }) {
  const access = usePublicAccess("assistant")
  const transport = new DefaultChatTransport<DiscoveryMessage>({ api:"/api/assistant",
    prepareSendMessagesRequest:({messages}) => ({body:{messages:analysisHistory(messages)}}),
    fetch: async (input, init) => {
      const response = await fetch(input, init)
      if (!response.ok) { try { await responseJson(response.clone()) } catch(error) { access.onDenied(error); throw error } }
      return response
    },
  })
  const [editedSpecification, setEditedSpecification] = useState<{ key: string; brief: DatasetBrief } | null>(null)
  const [prompt, setPrompt] = useState(autoRun ? "" : initialPrompt ?? "")
  const { messages, sendMessage, status, stop, error, setMessages } = useChat<DiscoveryMessage>({ transport, onError: error => toast.error(extractApiError(error)) })
  useEffect(() => {
    if (access.enabled && autoRun && initialPrompt?.trim() && consumeDiscoveryLaunch()) void sendMessage({ text: initialPrompt })
  }, [autoRun, initialPrompt, sendMessage, access.enabled])
  const busy = status === "submitted" || status === "streaming"
  const send = (text: string) => {
    if (!access.enabled || !text.trim() || busy) return
    if (text.length > 7800) { toast.error("The definition is too long. Shorten field descriptions before building."); return }
    void sendMessage({ text }); setPrompt("")
  }
  const latest = messages.filter(message => message.role === "assistant").findLast(message => analysisView(message).presentation)
  const presentation = latest ? analysisView(latest).presentation : undefined
  const definitionKey = `${latest?.id}-${JSON.stringify(presentation?.brief)}`
  const edited = editedSpecification?.key === definitionKey ? editedSpecification.brief : undefined
  const onBuild = (brief: DatasetBrief) => send(`Build and validate a dataset using this edited specification. It replaces the previous definition. Preserve its fields, types, population and unresolved decisions. Return standalone SQL and validation evidence.\n${JSON.stringify(brief)}`)
  return <section className="dataset-discovery-layout grid items-start lg:grid-cols-12" aria-label="Dataset discovery">
    <div className="discovery-conversation flex min-w-0 flex-col gap-6 lg:col-span-8">
      <div className="discovery-conversation-heading flex items-center justify-between gap-4">{messages.length > 0 && <h2>Conversation</h2>}{access.message && <p role="status">{access.message} {access.data?.sql.enabled && <Link href="/sql">Open SQL</Link>}</p>}{messages.length > 0 && <Button variant="outline" size="sm" disabled={busy || !access.enabled} onClick={() => { setMessages([]); setPrompt(""); setEditedSpecification(null) }}><Plus />New dataset</Button>}</div>

      {messages.map((message, index) => {
        if (message.role === "user") {
          const text = message.parts.flatMap(part => part.type === "text" ? [part.text] : []).join("\n")
          return <Card key={message.id} className="discovery-user-message"><CardHeader><CardTitle>You</CardTitle></CardHeader><CardContent><p className="whitespace-pre-wrap">{text.includes('\n{"title":') ? "Use the updated dataset definition and rebuild." : text}</p></CardContent></Card>
        }
        const view = analysisView(message)
        const current = index === messages.length - 1
        return <section key={message.id} className="flex min-w-0 flex-col gap-4" aria-label="Periplus response">
          <strong>Periplus</strong>
          {view.presentation?.source_plan && <p>{view.presentation.source_plan.material}</p>}
          {view.presentation?.brief.open_questions.map(question => <p key={question}><strong>{question}</strong></p>)}
          {current ? <AnalysisAnswer message={message} running={busy} /> : <><p>{view.presentation?.outcome.next_step ?? view.finding}</p><details><summary>Previous result & activity</summary><AnalysisAnswer message={message} running={false} /></details></>}
        </section>
      })}
      {busy && <p role="status" className="flex items-center gap-2"><LoaderCircle className="animate-spin" />{presentation ? "Building and checking your dataset…" : "Inspecting available sources…"}</p>}
      {error && <p role="alert">{extractApiError(error)} Your definition remains available.</p>}
      {presentation?.outcome.status === "blocked" && <Button disabled={busy || !access.enabled} className="self-start" onClick={() => send(`Retry this dataset build. Inspect available sources again and preserve this definition:\n${JSON.stringify(edited ?? presentation.brief)}`)}>Retry build</Button>}
      {presentation?.source_plan && presentation.outcome.status === "designing" && !presentation.brief.open_questions.length && <Button disabled={busy || !access.enabled} className="self-start" onClick={() => send(`Continue building and validating this dataset without changing its intended scope.\n${JSON.stringify(edited ?? presentation.brief)}`)}>Continue build</Button>}
    <form className="discovery-composer" onSubmit={event => { event.preventDefault(); send(edited ? `${prompt}\nCurrent edited specification (use these fields and rules when applying my request):\n${JSON.stringify(edited)}` : prompt) }}>
      <div className="composer-caption"><label htmlFor="dataset-idea">{messages.length ? "Message" : "Describe your dataset"}</label></div>
      <Textarea className="composer-input" id="dataset-idea" placeholder={messages.length ? "Change a field, explain a relationship, or resolve an open decision…" : "A dataset of book listings, with a title, price and link to each listing…"} value={prompt} maxLength={4000} onChange={event => setPrompt(event.target.value)} rows={3} />
      <div className="composer-actions"><span>Define the rows. We’ll explore the sources.</span>{busy ? <Button type="button" variant="outline" onClick={() => stop()}><Square />Stop</Button> : <Button type="submit" disabled={!access.enabled || !prompt.trim()}>{messages.length ? "Send" : "Start discovering"}<ArrowUp /></Button>}</div>
    </form>
      {!messages.length && <div className="discovery-starters"><span>Or explore an idea</span><div className="flex flex-wrap gap-2">{starters.map(({ label, text }) => <Button key={label} variant="ghost" size="sm" disabled={!access.enabled || busy} onClick={() => send(text)}>{label}<ArrowUp /></Button>)}</div></div>}
      <details className="discovery-notes"><summary>About this workspace</summary><p>Uses collected pages. <Link href="/observatory#coverage">Inspect coverage</Link> or <Link href="/observatory">request missing sources</Link>.</p><p>The agent sees up to 20 rows per query; previews and CSV exports contain up to 1,000 returned rows. Definitions and sampled results go to the model provider. This workspace is temporary: download your definition and SQL before reloading. <Link href="/about#access">Access & data use</Link></p></details>
    </div>
    <aside aria-label="Your dataset definition" className="discovery-definition min-w-0 break-words lg:sticky lg:top-6 lg:col-span-4 lg:max-h-[calc(100dvh-3rem)] lg:overflow-y-auto">
      {presentation ? <DatasetSpecification key={definitionKey} draft={edited ?? presentation.brief} onChange={brief => setEditedSpecification({ key: definitionKey, brief })} busy={busy || !access.enabled} onBuild={onBuild} /> : <div className="discovery-definition-empty"><div className="discovery-definition-label"><Columns3 /><h2>Your dataset</h2><span>Draft</span></div><h3>An idea, taking shape.</h3><p>Your columns, sources and rules will live here as we discover what’s possible.</p><dl><div><dt>Columns</dt><dd>The fields you need</dd></div><div><dt>Sources</dt><dd>The material behind each row</dd></div><div><dt>Scope</dt><dd>What belongs in your dataset</dd></div></dl></div>}
      {edited && <p role="status">Unapplied edits. Rebuild to update the results.</p>}
    </aside>
  </section>
}
