"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { consumeDiscoveryLaunch } from "@/lib/discovery-launch"
import { useChat } from "@ai-sdk/react"
import { DefaultChatTransport } from "ai"
import { ArrowUp, Search, Square, ArrowUpRight, LoaderCircle, Database, Code2, Globe2, Plus } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { AnalysisAnswer } from "@/components/analysis-answer"
import { analysisHistory } from "@/lib/analysis-view"
import { extractApiError } from "@/lib/api"
import type { DiscoveryMessage } from "@/types/assistant"

const transport = new DefaultChatTransport<DiscoveryMessage>({ api: "/api/assistant", prepareSendMessagesRequest: ({ messages }) => ({ body: { messages: analysisHistory(messages) } }) })
const starters = [
  { label: "Design a research dataset", text: "I want to build a dataset for research using the web. Help me find a useful question given the current corpus.", detail: "Explore possibilities and define the rows", icon: Globe2 },
  { label: "Study the web over time", text: "Help me design a dataset to study changes in HTML structure over time. What can the retained observations support?", detail: "Check history, cohorts and measurements", icon: Code2 },
  { label: "Plan an analysis", text: "What analyses can I build from the available tables and current coverage?", detail: "Work from the schema and coverage", icon: Database },
]

export function DiscoveryChat({ initialPrompt, autoRun = false, onOpenSql }: { initialPrompt?: string; autoRun?: boolean; onOpenSql: (sql: string) => void }) {
  const [prompt, setPrompt] = useState(autoRun ? "" : initialPrompt ?? "")
  const { messages, sendMessage, status, stop, error, setMessages } = useChat<DiscoveryMessage>({ transport, onError: error => toast.error(extractApiError(error)) })
  useEffect(() => {
    if (autoRun && initialPrompt?.trim() && consumeDiscoveryLaunch()) void sendMessage({ text: initialPrompt })
  }, [autoRun, initialPrompt, sendMessage])
  const busy = status === "submitted" || status === "streaming"
  const send = (text: string) => { if (text.trim() && !busy) { void sendMessage({ text }); setPrompt("") } }
  return <section className={`discovery-shell agent-workspace ${messages.length ? "has-conversation" : ""}`} aria-label="Ask workspace">
    <div className="agent-toolbar"><h2>{messages.length ? "Conversation" : "Design a dataset"}</h2>{messages.length > 0 && <Button variant="outline" size="sm" disabled={busy} onClick={() => setMessages([])}><Plus />New chat</Button>}</div>
    <div className="flex flex-col gap-7" aria-label="Conversation">{messages.map((message, messageIndex) => <article key={message.id} className={message.role === "user" ? "question-message" : "answer-message"}>
      <span className="message-label">{message.role === "user" ? "Your question" : "Periplus / analysis"}</span>
      {message.role === "user" ? message.parts.map((part, index) => part.type === "text" ? <p key={index} className="whitespace-pre-wrap leading-relaxed">{part.text}</p> : null) : <AnalysisAnswer message={message} onOpenSql={onOpenSql} running={busy && messageIndex === messages.length - 1} />}

    </article>)}</div>
    {busy && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" />Working on your analysis…</p>}
    {error && <p role="alert" className="text-sm text-destructive">{extractApiError(error)} You can edit your question or use the SQL bench.</p>}
    <div className="composer-dock">
      <form className="discovery-composer" onSubmit={event => { event.preventDefault(); send(prompt) }}>
        <div className="composer-caption"><span><Search />Explore the web as data</span><span>Question → dataset design → SQL</span></div>
        <Textarea aria-label="Ask Periplus" placeholder={messages.length ? "What would you change or clarify about the dataset?" : "What dataset would you like to build, and what will you use it for?"} value={prompt} maxLength={4000} onChange={event => setPrompt(event.target.value)} className="composer-input" rows={2} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(prompt) } }} />
        <div className="composer-actions"><span><Database /> Current corpus</span>{busy ? <Button type="button" variant="outline" aria-label="Stop response" onClick={() => stop()}><Square />Stop</Button> : <Button type="submit" aria-label="Ask Periplus" disabled={!prompt.trim()}>Explore <ArrowUp /></Button>}</div>
      </form>
      <p className="composer-footnote">Inspect the SQL and returned rows. Source links open live websites.</p>
    </div>
    {!messages.length && <>
      <p className="story-note">Design a dataset together, or start with a <Link className="story-link" href="/datasets">named dataset</Link>. <Link className="story-link" href="/coverage">See available sites and dates</Link> before choosing a question. Questions explore the existing corpus; they do not add new pages.</p>
      <div className="starter-grid">{starters.map(({ text, label, detail, icon: Icon }) => <Button className="starter-card" key={text} variant="ghost" onClick={() => send(text)}><Icon /><span><strong>{label}</strong><span>{detail}</span></span><ArrowUpRight /></Button>)}</div>
    </>}
    <p className="discovery-disclosure">Ask mode receives up to 20 rows per query and may inspect a smaller input sample. Check the SQL and scope before generalising. Questions and sampled results go to the model provider; conversations disappear on reload. <Link href="/about#access">Access & data use</Link></p>
  </section>
}
