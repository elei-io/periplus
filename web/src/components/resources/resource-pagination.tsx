import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"

import { Button } from "@/components/ui/button"

export const RESOURCE_PAGE_SIZE = 50

export function ResourcePagination({
  total,
  limit,
  offset,
  isFetching,
  onOffsetChange,
}: {
  total: number
  limit: number
  offset: number
  isFetching: boolean
  onOffsetChange: (offset: number) => void
}) {
  const currentPage = total === 0 ? 0 : Math.floor(offset / limit) + 1
  const pageCount = Math.ceil(total / limit)
  const start = total === 0 ? 0 : offset + 1
  const end = Math.min(offset + limit, total)

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-xs text-muted-foreground">
      <div>
        {start}-{end} of {total}
      </div>
      <div className="flex items-center gap-2">
        <span>
          Page {currentPage} of {pageCount || 0}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={isFetching || offset <= 0}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
        >
          <ChevronLeftIcon />
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={isFetching || offset + limit >= total}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Next
          <ChevronRightIcon />
        </Button>
      </div>
    </div>
  )
}
