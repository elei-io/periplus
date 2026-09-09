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
    captureAnalytics("landing_dataset_launched", { prompt_length: text.length })
    startTransition(() => router.push(`/discover?${new URLSearchParams({ run: "1", question: text })}`))
  }
  return <div className="landing-launcher">
    <form className="discovery-composer" onSubmit={event => { event.preventDefault(); launch() }}>
      <div className="launcher-toolbar"><strong>Describe your dataset</strong><span>Free to explore · No account needed</span></div>
      <Textarea ref={input} aria-label="Describe your dataset" className="composer-input" placeholder="For example: a list of websites, with one row per site and a count of pages in Periplus." value={value} maxLength={4000} onChange={event => setValue(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); launch() } }} rows={2} />
      <div className="composer-actions"><span><Database />Shared web data → your dataset</span><Button type="submit" disabled={pending || !value.trim()}>{pending ? "Opening…" : "Start building"}<ArrowUpRight /></Button></div>
    </form>
    <p className="composer-footnote">Start with the data already in Periplus. <Link href={sqlDraftLink(coverageSql)}>Explore websites in SQL.</Link></p>
    <div className="launcher-examples"><span>Try a starting point</span>{["List websites in Periplus, with page counts and first and last observation dates.", "Show links between websites, with one row per source and destination and a linking-page count."].map((text, i) => <Button key={text} variant="ghost" disabled={pending} onClick={() => { setValue(text); input.current?.focus() }}>{["Explore available websites", "Find links between websites"][i]}<ArrowUpRight /></Button>)}</div>
  </div>
}
