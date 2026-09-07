import Link from "next/link"
import { ArrowUpRight } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"

const uses = [
  { audience: "For analysts & data scientists", title: "Compare what you find.", description: "See product pages as rows you can compare. Choose the fields that matter and combine them with your own research, keeping sources and dates attached.", href: "/about#recipe", action: "Explore the book price example" },
  { audience: "For researchers & curious people", title: "Find what pages talk about.", description: "Start with a heading index, look for topic mentions, and follow the source URLs. Ask in ordinary language if SQL is new to you.", href: "/discover", action: "Explore page headings" },
  { audience: "For builders & AI teams", title: "Map connections between pages.", description: "Give your application or agent a queryable view of how pages connect. Inspect shared references through explicit SQL.", href: "/docs#examples", action: "Explore linked destinations" },
]

export function LandingStory() {
  return <div className="landing-story">
    <section className="story-section" aria-labelledby="spreadsheet-heading">
      <div className="story-heading"><span className="eyebrow">01 / Change your perspective</span><h2 id="spreadsheet-heading">Imagine the web<br />as a spreadsheet.</h2><p>A browser shows you a page. Imagine looking across pages as rows you can filter, compare, and join. Patterns that are hard to see page by page become questions you can ask of the data.</p><p>Compare pages, explore headings, or follow links. Your question determines the view.</p></div>
    </section>

    <section className="story-section" aria-labelledby="audiences-heading"><div className="story-heading"><span className="eyebrow">02 / Find your angle</span><h2 id="audiences-heading">One foundation.<br />Many ways to see it.</h2><p>Start with a question about the observed web. Compare pages, explore a topic, or follow connections through the same shared structure.</p></div><div className="audience-grid">{uses.map(item => <Card key={item.title} className="audience-card"><CardHeader><span className="eyebrow">{item.audience}</span><CardTitle><h3>{item.title}</h3></CardTitle><CardDescription>{item.description}</CardDescription></CardHeader><CardContent><Link className="story-link" href={item.href}>{item.action} <ArrowUpRight /></Link></CardContent></Card>)}</div></section>

    <section className="story-section" aria-labelledby="reuse-heading"><div className="story-heading"><span className="eyebrow">03 / The idea behind Periplus</span><h2 id="reuse-heading">Keep the structure.<br />Choose the meaning.</h2><p>Periplus treats the web as one dataset. A unified tabular model represents HTML elements, attributes, text, and links, keeping the structure available for questions we have not thought of yet.</p></div><div className="reuse-grid"><div><span className="eyebrow">01 / A unified model</span><h3>Different pages. Shared structure.</h3><p>Pages in the corpus use the same structural vocabulary. Their text stays connected to elements, attributes, and neighbouring content.</p></div><div><span className="eyebrow">02 / Meaning stays open</span><h3>You decide what matters.</h3><p>Define a product, a topic, or a relationship through your query. The shared model keeps the HTML structure; it does not require a fixed business schema.</p></div><div><span className="eyebrow">03 / A view you can inspect</span><h3>Make your perspective explicit.</h3><p>SQL records which fields and relationships define your view. Inspect the method, follow the sources, and export the rows to use in your own work.</p></div></div><div className="about-actions"><Link className="story-link" href="/about">The idea behind Periplus <ArrowUpRight /></Link><Link className="story-link" href="/about#recipe">See a worked extraction <ArrowUpRight /></Link></div></section>

    <div className="coverage-nudge"><span>What corner of the web would you bring into view?</span><Link className="story-link" href="/observatory">Suggest a starting point <ArrowUpRight /></Link></div>
  </div>
}
