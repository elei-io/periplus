import type { LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"

export function CatalogueHero({
  icon: Icon,
  eyebrow,
  title,
  description,
  children,
}: {
  icon: LucideIcon
  eyebrow: string
  title: string
  description: string
  children?: React.ReactNode
}) {
  return (
    <section className="relative shrink-0 overflow-hidden rounded-2xl border bg-card/70 px-5 py-5 shadow-[0_18px_60px_rgb(0_0_0/0.08)] backdrop-blur-xl sm:px-6">
      <div className="pointer-events-none absolute -top-20 -right-16 size-52 rounded-full bg-primary/10 blur-3xl" />
      <div className="relative flex flex-wrap items-end justify-between gap-5">
        <div className="flex min-w-0 items-start gap-4">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-2xl border border-primary/15 bg-primary/10 text-primary shadow-sm">
            <Icon className="size-5" />
          </span>
          <div className="min-w-0">
            <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-primary/80">{eyebrow}</div>
            <h1 className="mt-1 text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground sm:text-sm">{description}</p>
          </div>
        </div>
        {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
      </div>
    </section>
  )
}

export function CataloguePanel({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  return (
    <section className={cn("overflow-hidden rounded-2xl border bg-card/75 shadow-[0_18px_60px_rgb(0_0_0/0.07)] backdrop-blur-xl", className)}>
      {children}
    </section>
  )
}

export function CatalogueEmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon
  title: string
  description: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("grid min-h-72 place-items-center p-8 text-center", className)}>
      <div className="max-w-sm">
        <span className="mx-auto flex size-12 items-center justify-center rounded-2xl border border-dashed bg-muted/25 text-muted-foreground shadow-inner">
          <Icon className="size-5" />
        </span>
        <h2 className="mt-4 text-sm font-medium">{title}</h2>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
        {action && <div className="mt-4 flex justify-center">{action}</div>}
      </div>
    </div>
  )
}

export function CatalogueMetric({
  label,
  value,
  detail,
  icon: Icon,
  tone = "primary",
}: {
  label: string
  value: string
  detail?: string
  icon: LucideIcon
  tone?: "primary" | "emerald" | "amber" | "sky"
}) {
  const tones = {
    primary: "bg-primary/10 text-primary",
    emerald: "bg-emerald-500/10 text-emerald-500",
    amber: "bg-amber-500/10 text-amber-500",
    sky: "bg-sky-500/10 text-sky-500",
  }
  return (
    <div className="flex min-w-0 items-center gap-3 rounded-xl border bg-card/60 p-3 shadow-sm">
      <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-xl", tones[tone])}><Icon className="size-4" /></span>
      <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className="truncate text-base font-semibold tabular-nums">{value}</div>
        {detail && <div className="truncate text-[10px] text-muted-foreground">{detail}</div>}
      </div>
    </div>
  )
}
