import type { Metadata } from "next"
import { pageMetadata, publicOrigin } from "@/lib/seo"
import Link from "next/link"
import { ArrowDown, ArrowUpRight } from "lucide-react"
import { buttonVariants } from "@/components/ui/button"
import { LandingExample } from "@/components/landing-example"
import { LandingStory } from "@/components/landing-story"

export const metadata: Metadata = { ...pageMetadata("/", "Periplus — query the web as a dataset", "Explore Periplus’s shared web databank. Query shared tables of text, links, and HTML with SQL, or let an agent help build the dataset you need."), title: { absolute: "Periplus — query the web as a dataset" } }

export default function HomePage() {
  return <main className="discovery-shell landing-page">
    {publicOrigin && <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify({ "@context": "https://schema.org", "@type": "WebSite", name: "Periplus", url: publicOrigin, description: metadata.description }).replace(/</g, "\\u003c") }} />}
    <section className="discovery-hero">
      <span className="eyebrow"><span className="identity-dot" />A shared web databank</span>
      <h1>Query the web<br /><span>as a dataset.</span></h1>
      <p>Start with the pages already in Periplus. Query their text, links, and HTML with SQL,{" "}<br className="hidden sm:block" />or describe the dataset you need and let an agent help write the query.</p>
      <div className="flex flex-wrap justify-center gap-3 pt-4">
        <Link className={buttonVariants({ size: "lg" })} href="#build-dataset">Explore the data <ArrowDown aria-hidden="true" /></Link>
        <Link className={buttonVariants({ size: "lg", variant: "outline" })} href="/sql">Open SQL console <ArrowUpRight aria-hidden="true" /></Link>
      </div>
    </section>
    <LandingExample />
    <LandingStory />
  </main>
}
