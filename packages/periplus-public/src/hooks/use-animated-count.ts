"use client"

import { useEffect, useRef, useState } from "react"

/** Interpolate only between confirmed integer totals, without losing precision. */
export function useAnimatedCount(value: unknown, playing: boolean) {
  const target = /^\d+$/.test(String(value)) ? String(value) : null
  const [display, setDisplay] = useState<string | null>(target)
  const current = useRef(target)
  useEffect(() => {
    if (target === null) return
    let frame = 0
    const from = current.current === null ? BigInt(target) : BigInt(current.current)
    const to = BigInt(target)
    const started = performance.now()
    const animate = (now: number) => {
      const fraction = !playing || window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 1000 : Math.min(1000, Math.round((now - started) / 1.2))
      const next = String(from + (to - from) * BigInt(fraction) / BigInt(1000))
      current.current = next
      setDisplay(next)
      if (fraction < 1000 && next !== target) frame = window.requestAnimationFrame(animate)
    }
    frame = window.requestAnimationFrame(animate)
    return () => window.cancelAnimationFrame(frame)
  }, [target, playing])
  return display
}
