import Link from "next/link"
import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { DatabaseIcon } from "lucide-react"

export default function ToolLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-svh min-h-0 flex-col">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b px-4">
        <Link href="/" className="inline-flex items-center gap-2 font-medium">
          <DatabaseIcon className="size-4" aria-hidden="true" />
          <span>periplus</span>
        </Link>
        <Badge variant="outline">SQL terminal</Badge>
        <div className="ml-auto flex items-center gap-2">
          <Button
            nativeButton={false}
            variant="ghost"
            render={<Link href="/docs/sql" />}
          >
            SQL docs
          </Button>
          <Button
            nativeButton={false}
            variant="outline"
            render={<Link href="/domains" />}
          >
            Explore
          </Button>
        </div>
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  )
}
