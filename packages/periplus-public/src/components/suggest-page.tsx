"use client"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { CoverageRequestForm } from "@/components/coverage-request-form"
import { CoverageRequestActivity, CoverageRequestDetail } from "@/components/coverage-request-activity"
export function SuggestPage({ initialId }: { initialId?: string }) {
  const router = useRouter()
  return <main className="coverage-page"><span className="eyebrow">Corpus / Suggest coverage</span><h1>What data would make<br />your analysis possible?</h1><p className="text-muted-foreground">Help bring another corner of the web into view. Suggest a starting URL or describe the data you need, then follow its request here.</p><p className="story-note">Looking for data already here? <Link className="story-link" href="/coverage">Browse current coverage</Link>.</p><div className="flex flex-col gap-10"><CoverageRequestForm onCreated={id => router.push(`/suggest?request=${id}#request`)} />{initialId && <CoverageRequestDetail id={initialId} />}<CoverageRequestActivity /></div></main>
}
