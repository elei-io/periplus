import type { CollectionArrivalsPage } from "../types/frontier-items"

type Arrival = CollectionArrivalsPage["items"][number]

/** A bounded presentation queue; request totals always come from the server. */
export class RequestTailBuffer {
  private visible: Arrival[] = []
  private pending: Arrival[] = []
  private seen = new Set<string>()
  private initialized = false

  accept(newestFirst: Arrival[]): Arrival[] {
    const latest = new Map<string, Arrival>()
    for (const item of newestFirst) {
      if (!latest.has(item.observation_id)) latest.set(item.observation_id, item)
    }
    // Late recording/readiness updates refresh rows without replaying arrivals.
    this.visible = this.visible.map(item => latest.get(item.observation_id) ?? item)
    this.pending = this.pending.map(item => latest.get(item.observation_id) ?? item)
    if (!this.initialized) {
      this.visible = [...latest.values()].slice(0, 5)
      this.initialized = true
    } else {
      const incoming = [...latest.values()].filter(item => !this.seen.has(item.observation_id))
      this.pending.push(...incoming.reverse())
      this.pending = this.pending.slice(-50)
    }
    for (const id of latest.keys()) this.seen.add(id)
    while (this.seen.size > 1000) this.seen.delete(this.seen.values().next().value!)
    return this.visible
  }

  advance(immediate = false): Arrival[] | null {
    if (!this.pending.length) return null
    const incoming = this.pending.splice(0, immediate ? this.pending.length : 1)
    this.visible = [...incoming.reverse(), ...this.visible].slice(0, 5)
    return this.visible
  }
}
