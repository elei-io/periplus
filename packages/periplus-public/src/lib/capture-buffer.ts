import type { RecentCapture } from "../types/live"

export interface CaptureBufferSnapshot {
  visible: RecentCapture[]
  pending: number
  burst: number
}

/** Presentation only: ingestion/capture counts never come from this queue. */
export class CaptureBuffer {
  private visible: RecentCapture[] = []
  private pending: RecentCapture[] = []
  private seen = new Set<string>()
  private initialized = false
  private deadline = 0
  private nextAt = 0
  private burst = 0

  accept(items: RecentCapture[], now: number, reset = false): CaptureBufferSnapshot {
    if (!this.initialized || reset) {
      this.visible = items.slice(-7).reverse()
      this.pending = []
      this.seen = new Set(items.map(item => item.observation_id))
      this.initialized = true
      this.burst = 0
      return this.snapshot()
    }
    const incoming = items.filter(item => {
      if (this.seen.has(item.observation_id)) return false
      this.seen.add(item.observation_id)
      return true
    })
    if (incoming.length) {
      if (!this.pending.length) {
        this.deadline = now + 2500
        this.nextAt = now + 300
        this.burst = 0
      }
      this.pending.push(...incoming)
      if (this.pending.length > 1000) {
        const catchup = this.pending.splice(0, this.pending.length - 1000)
        this.visible = [...catchup.reverse(), ...this.visible].slice(0, 7)
        this.burst += catchup.length
      }
    }
    // The reader advances a cursor, so overlap only comes from bounded retries.
    // Keep recent duplicate protection bounded during indefinitely open tabs.
    while (this.seen.size > 4096) this.seen.delete(this.seen.values().next().value!)
    return this.snapshot()
  }

  tick(now: number, immediate = false): CaptureBufferSnapshot | null {
    if (!this.pending.length || (!immediate && now < this.nextAt)) return null
    const slots = Math.max(1, Math.floor((this.deadline - now) / 300))
    const take = immediate ? this.pending.length : Math.ceil(this.pending.length / slots)
    const arrivals = this.pending.splice(0, take)
    this.visible = [...arrivals.reverse(), ...this.visible].slice(0, 7)
    this.burst += arrivals.length
    this.nextAt = now + Math.min(750, Math.max(300, (this.deadline - now) / Math.max(1, this.pending.length)))
    return this.snapshot()
  }

  snapshot(): CaptureBufferSnapshot {
    return {visible: this.visible, pending: this.pending.length, burst: this.burst}
  }
}
