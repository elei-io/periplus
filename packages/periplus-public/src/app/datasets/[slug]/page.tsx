import type { Metadata } from "next"
import Link from "next/link"
import { notFound } from "next/navigation"
import { datasets } from "@/lib/datasets"
import { DatasetActions, DatasetPreview, DatasetSql } from "@/components/dataset-preview"

export function generateStaticParams() { return datasets.map(({ slug }) => ({ slug })) }
export async function generateMetadata({ params }: PageProps<"/datasets/[slug]">): Promise<Metadata> {
  const { slug } = await params
  const dataset = datasets.find(item => item.slug === slug)
  return { title: dataset?.name ?? "Dataset not found", description: dataset?.description }
}
export default async function DatasetPage({ params }: PageProps<"/datasets/[slug]">) {
  const { slug } = await params
  const dataset = datasets.find(item => item.slug === slug)
  if (!dataset) notFound()
  return <main className="about-page dataset-detail">
    <header className="about-hero"><Link className="story-link" href="/datasets">← All datasets</Link><span className="eyebrow">{dataset.category}</span><h1>{dataset.name}</h1><p>{dataset.description}</p><DatasetActions dataset={dataset} /></header>
    <dl className="dataset-scope"><div><dt>What one row means</dt><dd>{dataset.grain}</dd></div><div><dt>Source & scope</dt><dd>{dataset.scope}</dd></div></dl>
    <DatasetPreview dataset={dataset} />
    <section className="dataset-method"><span className="eyebrow">The dataset definition</span><h2>SQL you can inspect and change.</h2><DatasetSql sql={dataset.sql} /><DatasetActions dataset={dataset} /><p className="story-note">Opening this in SQL creates an editable draft. Run it there to apply your changes. A saved query preserves the method; it does not freeze the data.</p></section>
    <div className="about-actions"><Link className="story-link" href="/about#recipe">How to adapt an extraction</Link><Link className="story-link" href="/coverage">Inspect corpus coverage</Link><Link className="story-link" href="/about#access">Preview access & data use</Link></div>
  </main>
}
