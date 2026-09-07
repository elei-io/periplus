import { StartWindow } from "@/components/start-window"
import type { AdmissionWait } from "@/types/collections"

export function AdmissionWaitSummary({ value }: { value: AdmissionWait }) {
  return <section className="flex flex-col gap-2" aria-label="Admission waiting">
    <p>{value.pending_candidates} frozen candidate entries awaiting admission. Candidates may overlap or be excluded; this is not a count of promised new pages.</p>
    {value.estimate ? <StartWindow estimate={value.estimate} label="First admission" /> : <p>First-admission estimate unavailable: {value.estimate_unavailable_reason?.replaceAll("_", " ")}.</p>}
    {value.pending_candidates > 0 && <>
      <p>{value.elapsed_seconds === null ? "Selection wait duration is unavailable." : `Oldest selection has waited ${Math.floor(value.elapsed_seconds)} seconds as of this snapshot.`}</p>
      {value.preview_urls.length > 0 && <details><summary>Waiting candidate preview</summary><p>A bounded preview, not a scheduling order.</p><ul>{value.preview_urls.map((url, index) => <li key={`${index}:${url}`} className="break-all">{url}</li>)}</ul></details>}
    </>}
  </section>
}
