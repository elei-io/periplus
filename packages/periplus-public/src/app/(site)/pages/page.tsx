import type { Metadata } from "next"

import { CatalogueEmptyState } from "@/components/catalogue/catalogue-empty-state"
import { PageIntro } from "@/components/site/page-intro"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export const metadata: Metadata = {
  title: "Pages",
  description: "Explore URL observation history in Periplus.",
}

export default function PagesPage() {
  return (
    <main className="mx-auto w-full max-w-7xl flex-1 space-y-8 px-4 py-12 sm:px-6 lg:px-8">
      <PageIntro
        eyebrow="Public explorer"
        title="Pages"
        description="Search normalized URLs and inspect their observation history. The interface says pages; the evidence contract remains explicitly URL-based."
        actions={<Badge variant="outline">Observed URLs</Badge>}
      />

      <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
        <Input
          aria-label="Search pages"
          placeholder="Search URLs or hostnames"
          disabled
        />
        <Select defaultValue="recent" disabled>
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="recent">Most recently observed</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="overflow-hidden rounded-lg ring-1 ring-foreground/10">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>URL</TableHead>
              <TableHead>Hostname</TableHead>
              <TableHead>Last observed</TableHead>
              <TableHead>Latest outcome</TableHead>
              <TableHead>Observations</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                Page evidence will appear here after the bounded public API is defined.
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>

      <CatalogueEmptyState
        title="No page summaries are fabricated"
        description="Periplus currently exposes observations and immutable content rather than a mutable page dimension. This route is ready for a typed URL-summary API without pretending that API already exists."
      />
    </main>
  )
}
