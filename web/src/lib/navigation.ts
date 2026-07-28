import {
  ChartNoAxesCombinedIcon,
  GitForkIcon,
  ShieldCheckIcon,
  Globe2Icon,
  CalendarClockIcon,
  DatabaseZapIcon,
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
        name: "Graphs",
        href: "/crawls/graphs",
        icon: GitForkIcon,
        title: "Crawl Graphs",
        description: "Compose and run page-acquisition graphs.",
      },
      {
        name: "Schedules",
        href: "/crawls/schedules",
        icon: CalendarClockIcon,
        title: "Crawl Schedules",
        description: "Run crawl graphs automatically on intervals or cron.",
      },
      {
        name: "Metrics",
        href: "/crawls/metrics",
        icon: ChartNoAxesCombinedIcon,
        title: "Crawl Metrics",
        description: "See page acquisition progress, capacity, and graph runs.",
      },
    ],
  },
  {
    name: "Settings",
    slug: "settings",
    items: [
      {
        name: "Content policies",
        href: "/crawl-policies",
        icon: ShieldCheckIcon,
        title: "Policies",
        description:
          "Control response handling and rendered-content completion.",
      },
      {
        name: "Domain politeness",
        href: "/domain-policies",
        icon: Globe2Icon,
        title: "Domain Politeness",
        description: "Limit concurrent and paced requests to websites.",
      },
    ],
  },
]

export const defaultNavigationItem = navigationGroups[0].items[0]

export function findNavigationItem(pathname: string) {
  if (pathname.startsWith("/crawls/graphs/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/crawls/graphs")
  }
  if (pathname.startsWith("/crawls/schedules/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/crawls/schedules")
  }
  if (pathname.startsWith("/crawl-policies/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/crawl-policies")
  }
  return navigationGroups
    .flatMap((group) => group.items)
    .find((item) => item.href === pathname)
}
