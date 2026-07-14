import { ArrowUpRightIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export function DocsPage() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 pb-8">
      <div className="grid gap-3 md:grid-cols-3">
        <DocLink
          href="#scaling"
          title="Scaling Atlas"
          detail="Read the limiting resource and change the right capacity."
        />
        <DocLink
          href="#run-status"
          title="Run status"
          detail="Understand acquisition, cooldown, warnings, and errors."
        />
        <DocLink
          href="#querying"
          title="Querying the catalogue"
          detail="SQL performance and the system table reference."
        />
      </div>

      <section id="scaling" className="scroll-mt-6">
        <Card>
          <CardHeader>
            <CardTitle>Scaling Atlas</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-md border">
              <Table className="min-w-[48rem]">
                <TableHeader className="bg-muted/30">
                  <TableRow className="hover:bg-transparent">
                    <TableHead>First full resource</TableHead>
                    <TableHead>Change</TableHead>
                    <TableHead>Before increasing it</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <GuideRow
                    resource="Website access"
                    action="Raise that courtesy group’s limit"
                    check="Confirm the website permits more concurrency"
                  />
                  <GuideRow
                    resource="Fetch workers"
                    action="Add replicas for the full transport"
                    check="Confirm website limits still have headroom"
                  />
                  <GuideRow
                    resource="Catalogue"
                    action="Raise the catalogue budget, then add catalogue workers"
                    check="Benchmark DuckLake and object storage"
                  />
                  <GuideRow
                    resource="Object storage"
                    action="Increase MinIO or disk throughput, then its budget"
                    check="Measure read/write latency and throughput"
                  />
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      </section>

      <section id="run-status" className="scroll-mt-6">
        <Card>
          <CardHeader>
            <CardTitle>Run status</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <Definition term="Running" definition="Requests may be queued, fetching over the network, ingesting into the catalogue, or producing graph-edge work." />
            <Definition term="Cooldown" definition="Acquisition is complete; affected materialized views are catching up." />
            <Definition term="Warning" definition="A website or remote provider did not return a usable page." />
            <Definition term="Error" definition="Atlas failed during admission, ingestion, navigation, materialization, or run lifecycle." />
          </CardContent>
        </Card>
      </section>

      <section id="querying" className="scroll-mt-6">
        <Card>
          <CardHeader>
            <CardTitle>Querying the catalogue</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2">
            <RoadmapCard title="Writing efficient SQL" />
            <RoadmapCard title="Catalogue tables and columns" />
          </CardContent>
        </Card>
      </section>
    </div>
  )
}

function DocLink({ href, title, detail }: { href: string; title: string; detail: string }) {
  return (
    <a href={href} className="rounded-lg border bg-card p-4 transition-colors hover:bg-muted/40">
      <div className="flex items-center justify-between gap-3">
        <p className="font-medium">{title}</p>
        <ArrowUpRightIcon className="size-4 text-muted-foreground" />
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{detail}</p>
    </a>
  )
}

function GuideRow({ resource, action, check }: { resource: string; action: string; check: string }) {
  return (
    <TableRow>
      <TableCell className="font-medium">{resource}</TableCell>
      <TableCell>{action}</TableCell>
      <TableCell className="text-muted-foreground">{check}</TableCell>
    </TableRow>
  )
}

function Definition({ term, definition }: { term: string; definition: string }) {
  return (
    <div className="rounded-md border p-4">
      <p className="font-medium">{term}</p>
      <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{definition}</p>
    </div>
  )
}

function RoadmapCard({ title }: { title: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-dashed p-4">
      <span className="font-medium">{title}</span>
      <Badge variant="outline">Next</Badge>
    </div>
  )
}
