import {
  ChartNoAxesCombinedIcon,
  GitForkIcon,
  ShieldCheckIcon,
  Globe2Icon,
  CalendarClockIcon,
  SquareTerminalIcon,
} from "lucide-react"

import type { NavigationGroup } from "@/types/navigation"

export const navigationGroups: NavigationGroup[] = [
  {
    name: "SQL",
    slug: "sql",
    items: [
      {
        name: "Console",
        href: "/sql",
        icon: SquareTerminalIcon,
        title: "SQL Console",
        description: "Query ingest and material relations.",
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
        description:
          "See what each graph is doing and whether its data is up to date.",
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
