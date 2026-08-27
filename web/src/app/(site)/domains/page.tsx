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
  title: "Domains",
  description: "Explore hostnames observed by Periplus.",
}

export default function DomainsPage() {
  return (
    <main className="mx-auto w-full max-w-7xl flex-1 space-y-8 px-4 py-12 sm:px-6 lg:px-8">
      <PageIntro
        eyebrow="Public explorer"
        title="Domains"
        description="Explore exact hostnames derived from durable URL observations. Domain summaries will remain evidence views, not mutable domain records."
        actions={<Badge variant="outline">Last observed, not freshness</Badge>}
      />

      <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
        <Input
          aria-label="Search domains"
          placeholder="Search hostnames"
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
              <TableHead>Hostname</TableHead>
              <TableHead>Known URLs</TableHead>
              <TableHead>Observations</TableHead>
              <TableHead>Last observed</TableHead>
              <TableHead>Retained content</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                Domain data will appear here after the bounded public API is defined.
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>

      <CatalogueEmptyState
        title="The explorer contract comes next"
        description="The current catalogue can derive hostnames from observation URLs, but the public listing needs an explicit bounded API and measured query plan before it is connected."
      />
    </main>
  )
}
