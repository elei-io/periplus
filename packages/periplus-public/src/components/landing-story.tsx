import Link from "next/link"
import { ArrowUpRight, Files, ArrowRight } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { buildLink } from "@/lib/workspace-links"
import { LandingLauncher } from "@/components/landing-launcher"
import { coverageSql, datasets } from "@/lib/datasets"
import { sqlDraftLink } from "@/lib/schema-reference"

export function LandingStory() {
  return <div className="landing-story">
    <section className="story-section grid items-start gap-8 md:grid-cols-2" aria-labelledby="structure-heading">
      <div className="story-heading">
        <span className="eyebrow">One shared corpus</span>
        <h2 id="structure-heading">The same web data.<br />Your own questions.</h2>
        <p>The dataset you need may already be in Periplus, waiting for the right query. Shared tables preserve pages’ text, links, attributes, and HTML relationships so you can decide what to extract and compare.</p>
      </div>
      <div className="story-heading">
        <div className="corpus-branch" aria-label="The same shared pages can produce prices, headings, or link relationships">
          <div className="corpus-origin"><Files aria-hidden="true" /><span>Shared pages<small>Text · links · HTML</small></span></div>
          <div className="corpus-outputs">{["Book prices", "Page headings", "Link relationships"].map(label => <div key={label}><ArrowRight aria-hidden="true" /><span>{label}</span></div>)}</div>
        </div>
        <p>Ask a different question of the same pages. Keep the source URL and collection date with every result.</p>
        <div className="about-actions">
          <Link className="story-link" href={sqlDraftLink(coverageSql)}>Explore websites in SQL <ArrowUpRight aria-hidden="true" /></Link>
          <Link className="story-link" href="/docs#joins">See how the tables connect <ArrowUpRight aria-hidden="true" /></Link>
        </div>
      </div>
    </section>

    <section id="explore-data" className="story-section" aria-labelledby="explore-heading">
      <div className="story-heading">
        <span className="eyebrow">Try it</span>
        <h2 id="explore-heading">Start with a question.</h2>
        <p>Discover investigates the data already in Periplus, shows supporting evidence, and helps you understand what questions it can answer. If a dataset would help, you can take a suggestion into Build.</p>
      </div>
      <LandingLauncher />
    </section>

    <section className="story-section" aria-labelledby="examples-heading">
      <div className="story-heading">
        <span className="eyebrow">Start with a query</span>
        <h2 id="examples-heading">A few places to start.</h2>
      </div>
      <div className="audience-grid">
        {datasets.slice(0, 3).map((dataset, index) => <Card key={dataset.slug} className="audience-card">
          <CardHeader>
            <CardTitle><h3>{["Book prices", "Page headings", "Links between pages"][index]}</h3></CardTitle>
            <CardDescription>{[
              "Extract titles and prices from a practice website’s book pages in Periplus, keeping the source URL with each result.",
              "Make an index of page headings and their sources to compare topics across pages in Periplus.",
              "Find destinations linked from pages in Periplus and count how many distinct pages refer to each one.",
            ][index]}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="example-preview" aria-label="Illustrative output preview">
              <span>{["title / price_gbp", "heading / source", "from → to / pages"][index]}</span>
              <code>{["A Field Guide / 18.00", "Chapter one / example.org", "journal → archive / 12"][index]}</code>
              <small>Illustrative output</small>
            </div>
            <Link className="story-link" href={buildLink(`Help me adapt this dataset: ${dataset.name}.\nRow meaning: ${dataset.grain}\nScope: ${dataset.scope}\nSQL:\n${dataset.sql}`)}>Adapt this dataset <ArrowUpRight aria-hidden="true" /></Link>
            <Link className="story-link" href={sqlDraftLink(dataset.sql)}>Open editable query <ArrowUpRight aria-hidden="true" /></Link></CardContent>
        </Card>)}
      </div>
      <p className="story-note">Examples open as SQL drafts. Run them to see results from the currently available data.</p>
    </section>

    <div className="coverage-nudge">
      <span>Missing a source you need? Help expand Periplus’s coverage.</span>
      <Link className="story-link" href="/coverage">Request coverage <ArrowUpRight aria-hidden="true" /></Link>
    </div>
  </div>
}
