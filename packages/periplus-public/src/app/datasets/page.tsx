import type { Metadata } from "next"
import Link from "next/link"
import { ArrowUpRight } from "lucide-react"
import { datasets } from "@/lib/datasets"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"

export const metadata: Metadata = { title: "Datasets", description: "Different views of one shared web corpus. Explore named SQL queries, inspect their rows, and adapt them to your own questions." }

export default function DatasetsPage() {
  return <main className="about-page datasets-page">
    <header className="about-hero"><span className="eyebrow">Datasets / Curated starting points</span><h1>Datasets to build on.</h1><p>Different questions. Different views of the same foundation. Here, a dataset is a SQL query with a name and a description. Explore its rows, inspect the method, and make the view your own.</p><div className="about-actions"><Link className="story-link" href="/coverage">See available coverage <ArrowUpRight /></Link><Link className="story-link" href="/about#recipe">Learn from a worked example <ArrowUpRight /></Link></div></header>
    <div className="dataset-grid">{datasets.map(dataset => <Card key={dataset.slug} className="dataset-card"><CardHeader><span className="eyebrow">{dataset.category}</span><CardTitle><h2><Link href={`/datasets/${dataset.slug}`}>{dataset.name}</Link></h2></CardTitle><CardDescription>{dataset.description}</CardDescription></CardHeader><CardContent><p className="dataset-grain"><span>What one row means</span>{dataset.grain}</p><Link className="story-link" href={`/datasets/${dataset.slug}`}>Explore dataset <ArrowUpRight /></Link></CardContent></Card>)}</div>
    <p className="story-note">Each dataset is a reusable view of the current corpus, defined in SQL rather than stored as a separate copy. The book example uses a practice site; the other queries work across the available sources.</p>
    <div className="coverage-nudge"><span>Have an analysis others could build on?</span><a className="story-link" href="mailto:ekku.leivonen@elei.io?subject=Periplus%20dataset%20idea">Send a dataset idea <ArrowUpRight /></a></div>
  </main>
}
