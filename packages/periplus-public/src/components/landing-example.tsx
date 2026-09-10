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
      {steps.map((step, index) => <Card key={step.title} className="landing-step">
        <CardHeader><CardDescription>0{index + 1} / {step.title}</CardDescription><CardTitle><h3>{step.question}</h3></CardTitle></CardHeader>
        <CardContent><p>{step.detail}</p></CardContent>
      </Card>)}
    </div>
    <Card className="landing-query-panel">
      <CardHeader><CardTitle><h3>Count AI mentions by domain</h3></CardTitle><CardDescription>Count “artificial intelligence” and standalone “AI” across each domain, using the latest capture of each page.</CardDescription></CardHeader>
      <CardContent className="flex min-w-0 flex-col gap-4">
        <SqlExample sql={landingSql} />
        <Table>
          <TableCaption>Illustrative data · fictional domains and counts</TableCaption>
          <TableHeader><TableRow><TableHead>domain</TableHead><TableHead>mentions</TableHead><TableHead>matching_pages</TableHead></TableRow></TableHeader>
          <TableBody>{[
            ["research.example.com", 284, 42],
            ["robotics.example.com", 196, 31],
            ["ventures.example.com", 153, 27],
            ["computing.example.com", 128, 24],
            ["healthtech.example.com", 97, 19],
            ["manufacturing.example.com", 76, 16],
            ["education.example.com", 58, 13],
            ["climate.example.com", 43, 11],
            ["logistics.example.com", 29, 8],
            ["agriculture.example.com", 17, 5],
          ].map(([domain, mentions, pages]) => <TableRow key={domain}><TableCell>{domain}</TableCell><TableCell>{mentions}</TableCell><TableCell>{pages}</TableCell></TableRow>)}</TableBody>
        </Table>
      </CardContent>
    </Card>
    <Link className="story-link example-guide" href={sqlDraftLink(coverageSql)}>Explore the shared websites <ArrowUpRight aria-hidden="true" /></Link>
  </section>
}
