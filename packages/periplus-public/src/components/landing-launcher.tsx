"use client"

import Link from "next/link"
import { useRef, useState, useTransition } from "react"
import { useRouter } from "next/navigation"
import { ArrowUpRight, Database } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { coverageSql } from "@/lib/datasets"
import { sqlDraftLink } from "@/lib/schema-reference"
import { captureAnalytics } from "@/lib/analytics"

export function LandingLauncher() {
  const router = useRouter()
  const input = useRef<HTMLTextAreaElement>(null)
  const [value, setValue] = useState("")
  const [pending, startTransition] = useTransition()
  function launch(text = value) {
    if (!text.trim() || pending) return
    captureAnalytics("landing_discovery_launched", { prompt_length: text.length })
    startTransition(() => router.push(`/discover?${new URLSearchParams({ run: "1", question: text })}`))
  }
  return <div className="landing-launcher">
    <form className="discovery-composer" onSubmit={event => { event.preventDefault(); launch() }}>
      <div className="launcher-toolbar"><strong>What would you like to find out?</strong><span>Free to explore · No account needed</span></div>
      <Textarea ref={input} aria-label="What would you like to find out?" className="composer-input" placeholder="Ask a question or describe what you want to investigate." value={value} maxLength={4000} onChange={event => setValue(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); launch() } }} rows={2} />
      <div className="composer-actions"><span><Database />Explore questions and evidence</span><Button type="submit" disabled={pending || !value.trim()}>{pending ? "Opening…" : "Explore data"}<ArrowUpRight /></Button></div>
    </form>
    <p className="composer-footnote">Already have a schema? <Link href="/build">Build a dataset.</Link> Prefer SQL? <Link href={sqlDraftLink(coverageSql)}>Explore websites directly.</Link></p>
    <div className="launcher-examples"><span>Try a starting point</span>{["What websites are available in Periplus, and how recent are their captures?", "What can the links between websites in Periplus tell me?"].map((text, i) => <Button key={text} variant="ghost" disabled={pending} onClick={() => { setValue(text); input.current?.focus() }}>{["Explore available websites", "Find links between websites"][i]}<ArrowUpRight /></Button>)}</div>
  </div>
}
