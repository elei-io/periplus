import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"

interface PageIntroProps {
  eyebrow?: string
  title: string
  description: string
  actions?: ReactNode
}

export function PageIntro({
  eyebrow,
  title,
  description,
  actions,
}: PageIntroProps) {
  return (
    <div className="flex flex-col gap-4 border-b pb-8 sm:flex-row sm:items-end sm:justify-between">
      <div className="max-w-3xl space-y-3">
        {eyebrow ? <Badge variant="outline">{eyebrow}</Badge> : null}
        <div className="space-y-2">
          <h1 className="text-3xl font-medium tracking-tight sm:text-4xl">
            {title}
          </h1>
          <p className="max-w-2xl text-sm/relaxed text-muted-foreground">
            {description}
          </p>
        </div>
      </div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </div>
  )
}
