import Link from "next/link"
import { ArrowUpRight } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export function LandingStory() {
  return <div className="landing-story">
    <section className="story-section" aria-labelledby="evidence-heading">
      <div className="story-heading"><span className="eyebrow">Explore with SQL</span><h2 id="evidence-heading">Query page text,<br />HTML structure and links.</h2><p>Find matching passages, turn page headings into rows, or see which websites link to the same destination. Use SQL to combine those details across collected pages.</p></div>
      <div className="audience-grid">
        <Card><CardHeader><CardTitle><h3>Find relevant passages</h3></CardTitle></CardHeader><CardContent>Search page text for a phrase or topic. Read matching passages to decide which pages deserve a closer look.</CardContent></Card>
        <Card><CardHeader><CardTitle><h3>Find related websites</h3></CardTitle></CardHeader><CardContent>See where pages link and which destinations recur across websites. Check whether a link appears in a directory, article, company card or footer.</CardContent></Card>
        <Card><CardHeader><CardTitle><h3>Check where a finding came from</h3></CardTitle></CardHeader><CardContent>See the source URL and when the page was captured. Inspect the stored HTML to check what was on the page at that time.</CardContent></Card>
      </div>
    </section>
    <section className="story-section" aria-labelledby="preserve-heading"><div className="story-heading"><span className="eyebrow">Keep room to change your mind</span><h2 id="preserve-heading">The next idea shouldn’t need<br />another scraper.</h2><p>Periplus keeps the captured HTML alongside queryable text, structure and links. Need another field? Return to the stored pages and change your query.</p><Link className="story-link" href="/about">How Periplus came to be <ArrowUpRight /></Link></div></section>
    <section className="story-section" aria-labelledby="reuse-heading">
      <div className="story-heading"><span className="eyebrow">Your sources. Everyone’s next question.</span><h2 id="reuse-heading">Research the websites<br />other people have collected.</h2><p>Pages added to Periplus are available for everyone to query. Explore someone else’s sources, or add websites for your own project and give others a new starting point.</p><Link className="story-link" href="/coverage">Add sources for everyone <ArrowUpRight /></Link></div>
    </section>
    <section className="story-section" aria-labelledby="project-heading">
      <div className="story-heading"><span className="eyebrow">CSV and Python SDK</span><h2 id="project-heading">Take the results<br />into your project.</h2><p>Download results as CSV, or run queries from your notebook or application with the Python SDK. Include source URLs and capture dates, and keep the SQL so you can inspect and reuse the method.</p><div className="about-actions"><Link className="story-link" href="/sql">Open SQL <ArrowUpRight /></Link><Link className="story-link" href="/docs#sdk">Python SDK <ArrowUpRight /></Link></div></div>
    </section>
    <section id="explore-data" className="story-section" aria-labelledby="explore-heading"><div className="story-heading"><span className="eyebrow">Try an investigation</span><h2 id="explore-heading">Need help writing the first query?</h2><p>Describe what you want to find in Periplus’s collected websites. Use Ask SQL in the console to draft a query, then review and run it.</p></div><Link className="story-link" href="/sql">Open the SQL console <ArrowUpRight /></Link></section>
    <div className="coverage-nudge"><div><h2>Add websites you’d like to research.</h2><p>Request a website or describe the sources you’re interested in. Periplus collects their pages into the shared collection, where you and others can query them.</p></div><Link className="story-link" href="/coverage">Add websites <ArrowUpRight aria-hidden="true" /></Link></div>
  </div>
}
