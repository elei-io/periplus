import {
  CalendarClockIcon,
  DoorOpenIcon,
  ListChecksIcon,
  ShieldCheckIcon,
  Globe2Icon,
  SlidersHorizontalIcon,
  DatabaseZapIcon,
  FileTextIcon,
  SquareTerminalIcon,
  HardDriveIcon,
  ArrowDownToLineIcon,
} from "lucide-react"

import type { NavigationGroup } from "@/types/navigation"

export const navigationGroups: NavigationGroup[] = [
  {
    name: "Data",
    slug: "data",
    items: [
      {
        name: "Storage",
        href: "/data/storage",
        icon: HardDriveIcon,
        title: "Storage",
        description: "Storage footprint and reclamation.",
      },
      {
        name: "Ingestion",
        href: "/data/ingestion",
        icon: ArrowDownToLineIcon,
        title: "Ingestion",
        description: "Evidence delivery and ingestor health.",
      },
      {
        name: "Materialization",
        href: "/data/materialization",
        icon: DatabaseZapIcon,
        title: "Materialization",
        description: "Derived projections, rebuilds and worker capacity.",
      },
    ],
  },
  {
    name: "Observatory",
    slug: "observatory",
    items: [
      { name: "Queries", href: "/observatory/queries", icon: SquareTerminalIcon, title: "Queries", description: "Query patterns, latency and failures." },
      {
        name: "Crawler",
        href: "/observatory/crawler",
        icon: Globe2Icon,
        title: "Crawler",
        description: "Current activity, worker readiness and crawl controls.",
      },
      {
        name: "Requests",
        href: "/observatory/requests",
        icon: FileTextIcon,
        title: "Requests",
        description: "Saved crawl configuration, sources and budgets.",
      },
      {
        name: "Schedules",
        href: "/observatory/schedules",
        icon: CalendarClockIcon,
        title: "Schedules",
        description: "Timing and limits for recurring requests.",
      },
      {
        name: "Executions",
        href: "/observatory/executions",
        icon: ListChecksIcon,
        title: "Executions",
        description: "Individual runs, progress and results.",
      },
      { name: "Access", href: "/observatory/access", icon: DoorOpenIcon, title: "Public access", description: "Public availability, admission rates and crawl options." },
      {
        name: "Capture policies",
        href: "/observatory/capture-policies",
        icon: ShieldCheckIcon,
        title: "Capture Policies",
        description:
          "Control response handling and rendered-content completion.",
      },
      {
        name: "Domain policies",
        href: "/observatory/domain-policies",
        icon: SlidersHorizontalIcon,
        title: "Domain Policies",
        description: "Limit concurrent and paced requests to websites.",
      },
    ],
  },
]

export const defaultNavigationItem = {
  name: "Console",
  href: "/",
  icon: SquareTerminalIcon,
  title: "SQL Console",
  description: "Execute administrative SQL directly against DuckLake.",
}

export function findNavigationItem(pathname: string) {
  if (pathname === "/") return defaultNavigationItem
  if (/^\/frontier\/items\/[^/]+$/.test(pathname)) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/observatory/crawler")
  }
  const section = pathname.match(/^\/observatory\/(requests|schedules|executions)\/[^/]+$/)?.[1]
  if (section) {
    return navigationGroups.flatMap((group) => group.items)
      .find((item) => item.href === `/observatory/${section}`)
  }
  if (pathname.startsWith("/observatory/capture-policies/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/observatory/capture-policies")
  }
  return navigationGroups
    .flatMap((group) => group.items)
    .find((item) => item.href === pathname)
}
