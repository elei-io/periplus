import Link from "next/link"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  ArrowRightIcon,
  DatabaseIcon,
  GlobeIcon,
  RefreshCcwIcon,
  TerminalIcon,
} from "lucide-react"

const query = `SELECT
  effective_url,
  observed_at,
  http_status_code
FROM web.observation
ORDER BY observed_at DESC
LIMIT 10;`

const capabilities = [
  {
    icon: GlobeIcon,
    title: "Observe the web",
    description:
      "Acquire pages through explicit crawl plans and retain what was actually observed.",
  },
  {
    icon: DatabaseIcon,
    title: "Preserve evidence",
    description:
      "Keep immutable content and observation history instead of replacing yesterday with today.",
  },
  {
    icon: TerminalIcon,
    title: "Query with SQL",
    description:
      "Use DuckDB against a small, portable catalogue of observations, content, elements, and links.",
  },
] as const

export default function HomePage() {
  return (
    <main>
      <section className="border-b">
        <div className="mx-auto grid max-w-7xl gap-10 px-4 py-20 sm:px-6 lg:grid-cols-[1.1fr_0.9fr] lg:px-8 lg:py-28">
          <div className="flex flex-col justify-center gap-6">
            <Badge variant="outline">A programmable web corpus</Badge>
            <div className="space-y-4">
              <h1 className="max-w-3xl text-4xl font-medium tracking-tight sm:text-6xl">
                Query the web with SQL.
              </h1>
              <p className="max-w-2xl text-base/relaxed text-muted-foreground sm:text-lg/relaxed">
                Periplus acquires web content, preserves its source evidence, and
                turns observations, immutable bytes, HTML structure, and links
                into queryable relations.
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <Button
                size="lg"
                nativeButton={false}
                render={<Link href="/sql" />}
              >
                Open SQL terminal
                <ArrowRightIcon data-icon="inline-end" />
              </Button>
              <Button
                size="lg"
                nativeButton={false}
                variant="outline"
                render={<Link href="/domains" />}
              >
                Explore domains
              </Button>
            </div>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Recent observations</CardTitle>
              <CardDescription>
                Query the public evidence catalogue with ordinary DuckDB SQL.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <pre className="overflow-x-auto rounded-md bg-muted p-4 text-xs/relaxed">
                <code>{query}</code>
              </pre>
            </CardContent>
          </Card>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
        <div className="grid gap-4 md:grid-cols-3">
          {capabilities.map((capability) => {
            const Icon = capability.icon
            return (
              <Card key={capability.title}>
                <CardHeader>
                  <Icon className="mb-3 size-4 text-muted-foreground" />
                  <CardTitle>{capability.title}</CardTitle>
                  <CardDescription>{capability.description}</CardDescription>
                </CardHeader>
              </Card>
            )
          })}
        </div>
      </section>

      <section className="mx-auto w-full max-w-7xl px-4 pb-20 sm:px-6 lg:px-8">
        <Card>
          <CardHeader>
            <RefreshCcwIcon className="mb-3 size-4 text-muted-foreground" />
            <CardTitle>Need fresher evidence?</CardTitle>
            <CardDescription>
              Crawl requests will let teams ask Periplus to observe the domains
              and pages that matter to them. Account and billing workflows are
              intentionally coming later.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" disabled>
              Request a crawl · coming soon
            </Button>
          </CardContent>
        </Card>
      </section>
    </main>
  )
}
