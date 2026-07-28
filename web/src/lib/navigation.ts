import {
  ChartNoAxesCombinedIcon,
  GitForkIcon,
  ShieldCheckIcon,
  Globe2Icon,
  CalendarClockIcon,
  DatabaseZapIcon,
  FileTextIcon,
  SquareTerminalIcon,
} from "lucide-react"

import type { NavigationGroup } from "@/types/navigation"

export const navigationGroups: NavigationGroup[] = [
  {
    name: "Data",
    slug: "data",
    items: [
      {
        name: "Console",
        href: "/sql",
        icon: SquareTerminalIcon,
        title: "SQL Console",
        description: "Query ingest and material relations.",
      },
      {
        name: "Documents",
        href: "/data/documents",
        icon: FileTextIcon,
        title: "Documents",
        description: "Browse acquired documents and their owned bytes.",
      },
      {
        name: "Metrics",
        href: "/data/metrics",
        icon: ChartNoAxesCombinedIcon,
        title: "Data Metrics",
        description:
          "See whether accepted evidence is ingested and query-ready.",
      },
      {
        name: "Materializations",
        href: "/materializations",
        icon: DatabaseZapIcon,
        title: "Materializations",
        description: "Backfill or rebuild fixed Atlas projections.",
      },
    ],
  },
  {
    name: "Crawls",
    slug: "crawls",
    items: [
      {
        name: "Plans",
        href: "/crawls/plans",
        icon: GitForkIcon,
        title: "Crawl Plans",
        description: "Compose and run reusable page-acquisition plans.",
      },
      {
        name: "Schedules",
        href: "/crawls/schedules",
        icon: CalendarClockIcon,
        title: "Crawl Schedules",
        description: "Run crawl plans automatically on intervals or cron.",
      },
      {
        name: "Content policies",
        href: "/content-policies",
        icon: ShieldCheckIcon,
        title: "Content Policies",
        description:
          "Control response handling and rendered-content completion.",
      },
      {
        name: "Domain policies",
        href: "/domain-policies",
        icon: Globe2Icon,
        title: "Domain Policies",
        description: "Limit concurrent and paced requests to websites.",
      },
      {
        name: "Metrics",
        href: "/crawls/metrics",
        icon: ChartNoAxesCombinedIcon,
        title: "Crawl Metrics",
        description: "See page acquisition progress, capacity, and crawl runs.",
      },
    ],
  },
]

export const defaultNavigationItem = navigationGroups[0].items[0]

export function findNavigationItem(pathname: string) {
  if (pathname.startsWith("/crawls/plans/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/crawls/plans")
  }
  if (pathname.startsWith("/crawls/schedules/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/crawls/schedules")
  }
  if (pathname.startsWith("/content-policies/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/content-policies")
  }
  return navigationGroups
    .flatMap((group) => group.items)
    .find((item) => item.href === pathname)
}
