import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { DatabaseIcon } from "lucide-react"

interface CatalogueEmptyStateProps {
  title: string
  description: string
}

export function CatalogueEmptyState({
  title,
  description,
}: CatalogueEmptyStateProps) {
  return (
    <Card>
      <CardHeader>
        <div className="mb-2 flex items-center justify-between gap-3">
          <DatabaseIcon className="size-4 text-muted-foreground" />
          <Badge variant="outline">API pending</Badge>
        </div>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="text-xs text-muted-foreground">
        This scaffold intentionally does not run an unbounded catalogue query or
        display invented production statistics.
      </CardContent>
    </Card>
  )
}
