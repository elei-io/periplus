"use client"

import { useState, useTransition } from "react"
import { useRouter } from "next/navigation"
import { ArrowUpRight, MessageSquare, Code2, Database } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import Link from "next/link"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

export function LandingLauncher() {
  const router = useRouter()
  const [mode, setMode] = useState("agent")
  const [drafts, setDrafts] = useState({ agent: "", sql: "" })
  const [pending, startTransition] = useTransition()
  const value = mode === "sql" ? drafts.sql : drafts.agent
  function launch(text = value) {
    if (!text.trim() || pending) return
    const params = new URLSearchParams({ mode, run: "1", [mode === "sql" ? "sql" : "question"]: text })
    startTransition(() => router.push(`/discover?${params}`))
  }
  return <div className="landing-launcher">
    <form className="discovery-composer" onSubmit={event => { event.preventDefault(); launch() }}>
      <div className="launcher-toolbar"><Tabs value={mode} onValueChange={value => setMode(String(value))}><TabsList aria-label="Input mode"><TabsTrigger value="agent"><MessageSquare />Ask</TabsTrigger><TabsTrigger value="sql"><Code2 />SQL</TabsTrigger></TabsList></Tabs><span>Free preview · No account needed</span></div>
      <Textarea aria-label={mode === "sql" ? "SQL query" : "Ask Periplus"} className={`composer-input ${mode === "sql" ? "font-mono" : ""}`} placeholder={mode === "sql" ? "SELECT requested_url FROM web.observation LIMIT 20;" : "What would you like to discover in the web?"} value={value} maxLength={mode === "sql" ? 10000 : 4000} onChange={event => setDrafts({ ...drafts, [mode]: event.target.value })} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && (mode === "agent" || event.metaKey || event.ctrlKey)) { event.preventDefault(); launch() } }} rows={2} />
      <div className="composer-actions"><span><Database />Explore the current corpus</span><Button type="submit" disabled={pending || !value.trim()}>{pending ? "Opening…" : "Explore"}<ArrowUpRight /></Button></div>
    </form>
    <p className="composer-footnote">Queries use the pages currently in Periplus. {mode === "agent" ? "Ask in ordinary language; SQL is optional." : "Read-only DuckDB SQL."}</p>
    <div className="launcher-examples"><span>Try a starting point</span>{(mode === "agent" ? ["Compare titles and prices from collected Books to Scrape product pages.", "Which sites are represented, and when were they collected?"] : ["SELECT requested_url, observed_at FROM web.observation LIMIT 20;", "DESCRIBE content.html_element;"]).map((text, i) => <Button key={text} variant="ghost" disabled={pending} onClick={() => launch(text)}>{mode === "sql" ? ["Explore available pages", "Inspect the HTML schema"][i] : ["Compare book prices", "See available sites"][i]}<ArrowUpRight /></Button>)}<Link className="story-link" href="/datasets">Browse datasets<ArrowUpRight /></Link></div>
  </div>
}
