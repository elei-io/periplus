import { coverageSql } from "@/lib/datasets"
import { sqlDraftLink } from "@/lib/schema-reference"
import Link from "next/link"
import { SqlExample } from "@/components/sql-example"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, TableCaption } from "@/components/ui/table"
import { landingSql } from "@/lib/landing-query"
import { ArrowUpRight } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

const steps = [
  { title: "Find the relevant pages", question: "Which firms mention artificial intelligence or AI?", detail: "Find pages mentioning “artificial intelligence” or “AI” and read the surrounding text to see what each firm says." },
  { title: "Follow their connections", question: "Where do their portfolio pages lead?", detail: "Find company website links on portfolio pages. Read the nearby headings and descriptions to understand each mention." },
  { title: "Compare the evidence", question: "Which destinations appear across firms?", detail: "Find destinations linked by more than one firm, then inspect the source pages for the relationship each firm describes." },
]

export function LandingExample() {
  return <section id="example" className="landing-example" aria-labelledby="example-heading">
    <div className="featured-dataset-heading"><div><span className="eyebrow">Start with a hunch</span><h2 id="example-heading">One collection of websites. Several ways to query it.</h2><p>Take a group of investment firm websites. Search their text for “artificial intelligence” or “AI,” list links from portfolio pages, or compare destinations linked by several firms.</p></div></div>
    <div className="audience-grid">
      {steps.map((step, index) => <Card key={step.title} className="landing-step w-full max-w-sm">
        <CardHeader><CardDescription>0{index + 1} / {step.title}</CardDescription><CardTitle><h3>{step.question}</h3></CardTitle></CardHeader>
        <CardContent><p>{step.detail}</p></CardContent>
      </Card>)}
    </div>
    <Card className="landing-query-panel">
      <CardHeader><CardTitle><h3>Discover pages about artificial intelligence</h3></CardTitle><CardDescription>Find up to 100 matching contents with a title, representative URL, and matching snippet.</CardDescription></CardHeader>
      <CardContent className="flex min-w-0 flex-col gap-4">
        <SqlExample sql={landingSql} />
        <Table>
          <TableCaption>Illustrative results · fictional pages</TableCaption>
          <TableHeader><TableRow><TableHead>title</TableHead><TableHead>url</TableHead><TableHead>snippet</TableHead><TableHead>score</TableHead></TableRow></TableHeader>
          <TableBody>{[
            ["Artificial intelligence research", "https://research.example.com/ai", "Artificial intelligence research", 1],
            ["Our investments", "https://ventures.example.com/portfolio", "Investing in artificial intelligence companies", 1],
            ["Latest news", "https://computing.example.com/news", "Our team studies artificial intelligence applications", 1],
          ].map(([title, url, snippet, score]) => <TableRow key={url}><TableCell>{title}</TableCell><TableCell>{url}</TableCell><TableCell>{snippet}</TableCell><TableCell>{score}</TableCell></TableRow>)}</TableBody>
        </Table>
      </CardContent>
    </Card>
    <Link className="story-link example-guide" href={sqlDraftLink(coverageSql)}>Explore the shared websites <ArrowUpRight aria-hidden="true" /></Link>
  </section>
}
