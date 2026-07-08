import {
  DatabaseIcon,
  ExternalLinkIcon,
  FileSearchIcon,
  SparklesIcon,
} from "lucide-react"

import {
  ContextMenuContent,
  ContextMenuGroup,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
} from "@/components/ui/context-menu"
import { resultActionHref } from "@/lib/result-actions"

type ResultLinkContextMenuContentProps = {
  url: string
}

export function ResultLinkContextMenuContent({
  url,
}: ResultLinkContextMenuContentProps) {
  return (
    <ContextMenuContent className="w-52">
      <ContextMenuGroup>
        <ContextMenuLabel>Result actions</ContextMenuLabel>
        <ContextMenuItem
          render={<a href={url} target="_blank" rel="noreferrer" />}
        >
          <ExternalLinkIcon />
          Open in a new tab
        </ContextMenuItem>
      </ContextMenuGroup>
      <ContextMenuSeparator />
      <ContextMenuGroup>
        <ContextMenuItem
          render={<a href={resultActionHref("/playground/index", url)} />}
        >
          <DatabaseIcon />
          Index this page
        </ContextMenuItem>
        <ContextMenuItem
          render={<a href={resultActionHref("/playground/extract", url)} />}
        >
          <SparklesIcon />
          Extract from this page
        </ContextMenuItem>
        <ContextMenuItem
          render={<a href={resultActionHref("/playground/crawl", url)} />}
        >
          <FileSearchIcon />
          Crawl this page
        </ContextMenuItem>
      </ContextMenuGroup>
    </ContextMenuContent>
  )
}
