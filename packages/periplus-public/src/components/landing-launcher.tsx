"use client"

import Link from "next/link"
import { useState, useTransition } from "react"
import { useRouter } from "next/navigation"
import { ArrowUpRight, Database } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

export function LandingLauncher() {
  const router = useRouter()
  const [value, setValue] = useState("")
  const [pending, startTransition] = useTransition()
  function launch(text = value) {
    if (!text.trim() || pending) return
    startTransition(() => router.push(`/discover?${new URLSearchParams({ run: "1", question: text })}`))
  }
  return <div className="landing-launcher">
    <form className="discovery-composer" onSubmit={event => { event.preventDefault(); launch() }}>
      <div className="launcher-toolbar"><strong>Discover your dataset</strong><span>Free to explore · No account needed</span></div>
      <Textarea aria-label="Describe your dataset" className="composer-input" placeholder="What dataset do you have in mind? Describe the records, fields, or sources you need." value={value} maxLength={4000} onChange={event => setValue(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); launch() } }} rows={2} />
      <div className="composer-actions"><span><Database />Web structure → your schema</span><Button type="submit" disabled={pending || !value.trim()}>{pending ? "Opening…" : "Start discovering"}<ArrowUpRight /></Button></div>
    </form>
    <p className="composer-footnote">Describe your dataset; the agent explores how collected material can produce it. <Link href="/sql">Prefer writing SQL? Open the SQL workspace.</Link></p>
    <div className="launcher-examples"><span>Try a starting point</span>{["Help me build a dataset of websites in the current public observations: one row per hostname, with distinct page count and first and last observation dates.", "Help me build a dataset of relationships between websites from collected links: one row per source and target hostname, with a distinct linking-page count."].map((text, i) => <Button key={text} variant="ghost" disabled={pending} onClick={() => launch(text)}>{["A source directory", "A website relationship table"][i]}<ArrowUpRight /></Button>)}</div>
  </div>
}
