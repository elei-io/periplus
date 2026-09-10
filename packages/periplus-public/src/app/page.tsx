import type { Metadata } from "next"
import { pageMetadata, publicOrigin } from "@/lib/seo"
import Link from "next/link"
import { ArrowUpRight } from "lucide-react"
import { buttonVariants } from "@/components/ui/button"
import { LandingExample } from "@/components/landing-example"
import { LandingStory } from "@/components/landing-story"

export const metadata: Metadata = { ...pageMetadata("/", "Periplus — the web, in tables", "Explore website content with SQL without building a new scraper for every idea. Use the pages others have collected, add sources that interest you, and keep refining your questions."), title: { absolute: "Periplus — the web, in tables" } }

export default function HomePage() {
  return <main className="discovery-shell landing-page">
    {publicOrigin && <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify({ "@context": "https://schema.org", "@type": "WebSite", name: "Periplus", url: publicOrigin, description: metadata.description }).replace(/</g, "\\u003c") }} />}
    <section className="discovery-hero">
      <h1>The web,<br /><span>in tables.</span></h1>
      <p>Explore website content with SQL without building a new scraper for every idea. Use the pages others have collected, add sources that interest you, and keep refining your questions.</p>
      <div className="flex flex-wrap justify-center gap-3 pt-4">
        <Link className={buttonVariants({ size: "lg" })} href="/sql">Explore the data <ArrowUpRight aria-hidden="true" /></Link>
        <Link className={buttonVariants({ size: "lg", variant: "outline" })} href="/coverage">Add websites <ArrowUpRight aria-hidden="true" /></Link>
      </div>
    </section>
    <LandingExample />
    <LandingStory />
  </main>
}
