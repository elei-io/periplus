"use client"
import { useEffect, useState } from "react"
import type { StartEstimate } from "@/types/frontier-items"

type WindowValues = Pick<StartEstimate, "earliest_at" | "latest_at" | "calculated_at" | "expires_at" | "sample_size">

export function StartWindow({ estimate, label = "Start" }: { estimate: WindowValues; label?: "Start" | "First page accepted" }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  if (now >= new Date(estimate.expires_at).getTime()) return <p>{label} estimate expired; awaiting refreshed conditions.</p>
  return <div className="flex flex-col gap-1">
    <p>Estimated {label.toLowerCase()}: {new Date(estimate.earliest_at).toLocaleTimeString()}–{new Date(estimate.latest_at).toLocaleTimeString()}.</p>
    <p className="text-sm text-muted-foreground">Based on {estimate.sample_size} recent waits under matching policies, calculated {new Date(estimate.calculated_at).toLocaleTimeString()}. Conditional on unchanged policies, worker readiness, and competing work; this is not a guaranteed {label.toLowerCase()} time or a reserved queue position.</p>
  </div>
}
