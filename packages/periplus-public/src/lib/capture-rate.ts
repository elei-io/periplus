import type { CapturePage } from "../types/live"

/** Rate of unique arrivals across complete live-feed polls, before animation. */
export class CaptureRate {
  private baseline: number | undefined
  private previous: number | undefined
  private seen = new Set<string>()
  private arrivals: { at: number; count: number }[] = []

  accept(page: CapturePage, now: number): number | undefined {
    // Bootstrap, catch-up, and interrupted polling are not live activity samples.
    if (this.previous === undefined || page.bootstrap || page.reset_reason || page.has_more || now - this.previous > 15000 || now <= this.previous) {
      this.baseline = page.has_more ? undefined : now
      this.arrivals = []
      this.seen = new Set(page.items.map(item => item.observation_id))
      this.previous = now
      return undefined
    }
    this.previous = now
    if (this.baseline === undefined) {
      this.baseline = now
      this.seen = new Set(page.items.map(item => item.observation_id))
      return undefined
    }
    let count = 0
    for (const item of page.items) {
      if (!this.seen.has(item.observation_id)) count++
      this.seen.add(item.observation_id)
    }
    while (this.seen.size > 4096) this.seen.delete(this.seen.values().next().value!)
    this.arrivals.push({at: now, count})
    this.arrivals = this.arrivals.filter(sample => sample.at > now - 60000)
    const elapsed = Math.min(60000, now - this.baseline)
    return this.arrivals.reduce((total, sample) => total + sample.count, 0) * 60000 / elapsed
  }
}
