import type { CurrentCollection } from "@/types/collections"

export function CollectionQueueSummary({ item }: { item: CurrentCollection }) {
  const queue = item.queue
  return <section className="flex flex-col gap-2" aria-label="Collection queue">
    <p>{queue.runnable_pages} runnable · {queue.deferred_pages} deferred · {queue.unknown_pages} with eligibility unknown</p>
    <p>Runnable counts reflect observed scheduling controls. Capacity is rechecked before capture.</p>
    <p>{queue.oldest_wait_seconds === null ? "No admitted pages waiting." : `Oldest admitted page has waited ${Math.floor(queue.oldest_wait_seconds)} seconds as of this snapshot.`}</p>
    {queue.constraints.length > 0 && <ul>{queue.constraints.map(constraint => <li key={constraint.reason}>{constraint.pages}: {constraint.reason.replaceAll("_", " ")}</li>)}</ul>}
    <p>Last progress: {item.last_progress_at ? new Date(item.last_progress_at).toLocaleString() : "unavailable"}</p>
  </section>
}
