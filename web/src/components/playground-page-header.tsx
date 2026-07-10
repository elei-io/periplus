import { ArrowRightIcon, BookmarkCheckIcon } from "lucide-react"

type PlaygroundPageHeaderProps = {
  title: string
  description: string
  taskNote: string
  taskHref: string
}

export function PlaygroundPageHeader({
  title,
  description,
  taskNote,
  taskHref,
}: PlaygroundPageHeaderProps) {
  return (
    <div className="flex flex-col items-start justify-between gap-4 md:flex-row md:items-end">
      <div className="grid max-w-[40rem] gap-2 text-left">
        <h1 className="text-2xl font-semibold tracking-tight md:text-[1.7rem]">
          {title}
        </h1>
        <p className="text-sm leading-6 text-muted-foreground md:text-[0.95rem]">
          {description}
        </p>
      </div>
      <a
        className="group flex shrink-0 items-center gap-2 rounded-full border bg-card/60 px-3 py-1.5 text-[11px] text-muted-foreground shadow-sm transition-colors hover:border-primary/25 hover:text-foreground"
        href={taskHref}
      >
        <BookmarkCheckIcon className="size-3.5 text-primary" />
        <span>{taskNote}</span>
        <ArrowRightIcon className="size-3 transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none" />
      </a>
    </div>
  )
}
