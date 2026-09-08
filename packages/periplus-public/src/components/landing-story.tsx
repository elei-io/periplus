import Link from "next/link"
import { ArrowUpRight } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"

const destinations = [
  { label: "SQL", title: "Ask the web a better question.", description: "Look across pages instead of opening them one by one. Turn headings, links, and attributes into rows you can filter, compare, and join.", preview: "Browse the catalogue, write a query, inspect the results, and export what you find.", href: "/sql", action: "Open the SQL console" },
  { label: "Discover", title: "What dataset do you have in mind?", description: "Start with an idea, even before you know the query. Explore the available material together and shape it into the fields and relationships your work needs.", preview: "Build an editable dataset definition alongside a conversation, then review the SQL and preview rows.", href: "/discover", action: "Build a dataset" },
  { label: "Observatory", title: "The web, coming into view.", description: "See the shared corpus grow, follow new observations, and help shape where it looks next. Your next dataset starts with the right source material.", preview: "Follow the observation feed, check current coverage, and suggest a starting URL.", href: "/observatory", action: "Explore the observatory" },
  { label: "Docs", title: "From collected pages to your dataset.", description: "Learn to read the web as data. Follow a worked extraction from source structure to useful rows, then adapt the method to your own question.", preview: "Find the schema, join keys, SQL examples, and query limits in one reference.", href: "/docs", action: "Read the documentation" },
  { label: "About", title: "One web. Many ways to see it.", description: "Keep the structure. Choose the meaning. A shared analytical foundation lets the same pages support questions nobody anticipated when they were collected.", preview: "Read the project’s vision and understand access, privacy, and how collected content can be used.", href: "/about", action: "Get to know Periplus" },
]

const uses = [
  { audience: "For analysts & data scientists", title: "Compare what you find.", description: "See product pages as rows you can compare. Choose the fields that matter and combine them with your own research, keeping sources and dates attached.", href: "/docs#recipe", action: "Explore the book price example" },
  { audience: "For researchers & curious people", title: "Find what pages talk about.", description: "Define a text index with the headings, passages, and source references your research needs. Refine its definition with the dataset builder.", href: "/discover", action: "Explore page headings" },
  { audience: "For builders & AI teams", title: "Map connections between pages.", description: "Give your application or agent a queryable view of how pages connect. Inspect shared references through explicit SQL.", href: "/docs#examples", action: "Explore linked destinations" },
]

export function LandingStory() {
  return <div className="landing-story">
    {destinations.map((page, index) => <section key={page.href} className="story-section grid gap-8 md:grid-cols-2" aria-labelledby={`preview-${page.label.toLowerCase()}`}>
      <div className="story-heading"><span className="eyebrow">{String(index + 1).padStart(2, "0")} / {page.label}</span><h2 id={`preview-${page.label.toLowerCase()}`}>{page.title}</h2><p>{page.description}</p></div>
      <Card className="self-center"><CardHeader><CardTitle>Inside {page.label}</CardTitle><CardDescription>{page.preview}</CardDescription></CardHeader><CardContent><Link className="story-link" href={page.href}>{page.action} <ArrowUpRight aria-hidden="true" /></Link></CardContent></Card>
    </section>)}

    <section className="story-section" aria-labelledby="spreadsheet-heading">
      <div className="story-heading"><span className="eyebrow">06 / Change your perspective</span><h2 id="spreadsheet-heading">Imagine the web<br />as a spreadsheet.</h2><p>A browser shows you a page. Imagine looking across pages as rows you can filter, compare, and join. Patterns that are hard to see page by page become questions you can ask of the data.</p><p>Compare pages, explore headings, or follow links. Your dataset definition determines the view.</p></div>
    </section>

    <section className="story-section" aria-labelledby="audiences-heading"><div className="story-heading"><span className="eyebrow">07 / Find your angle</span><h2 id="audiences-heading">One foundation.<br />Many ways to see it.</h2><p>Start with the dataset you need. Define records, extract fields, and build relationships through the same shared web structure.</p></div><div className="audience-grid">{uses.map(item => <Card key={item.title} className="audience-card"><CardHeader><span className="eyebrow">{item.audience}</span><CardTitle><h3>{item.title}</h3></CardTitle><CardDescription>{item.description}</CardDescription></CardHeader><CardContent><Link className="story-link" href={item.href}>{item.action} <ArrowUpRight /></Link></CardContent></Card>)}</div></section>

    <section className="story-section" aria-labelledby="reuse-heading"><div className="story-heading"><span className="eyebrow">08 / The idea behind Periplus</span><h2 id="reuse-heading">Keep the structure.<br />Choose the meaning.</h2><p>Periplus treats the web as one dataset. A unified tabular model represents HTML elements, attributes, text, and links, keeping the structure available for questions we have not thought of yet.</p></div><div className="reuse-grid"><div><span className="eyebrow">01 / A unified model</span><h3>Different pages. Shared structure.</h3><p>Pages in the corpus use the same structural vocabulary. Their text stays connected to elements, attributes, and neighbouring content.</p></div><div><span className="eyebrow">02 / Meaning stays open</span><h3>You decide what matters.</h3><p>Define a product, a topic, or a relationship through your query. The shared model keeps the HTML structure; it does not require a fixed business schema.</p></div><div><span className="eyebrow">03 / A view you can inspect</span><h3>Make your perspective explicit.</h3><p>SQL records which fields and relationships define your view. Inspect the method, follow the sources, and export the rows to use in your own work.</p></div></div><div className="about-actions"><Link className="story-link" href="/about">The idea behind Periplus <ArrowUpRight /></Link><Link className="story-link" href="/docs#recipe">See a worked extraction <ArrowUpRight /></Link></div></section>

    <div className="coverage-nudge"><span>What corner of the web would you bring into view?</span><Link className="story-link" href="/observatory">Suggest a starting point <ArrowUpRight /></Link></div>
  </div>
}
