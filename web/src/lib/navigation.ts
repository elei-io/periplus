import {
  BracesIcon,
  ChartNoAxesCombinedIcon,
  GitForkIcon,
  FileCode2Icon,
  ListFilterIcon,
  ShieldCheckIcon,
  TablePropertiesIcon,
  ViewIcon,
} from "lucide-react"

import type { NavigationGroup } from "@/types/navigation"

export const navigationGroups: NavigationGroup[] = [
  {
    name: "Catalogue",
    slug: "catalogue",
    items: [
      {
        name: "SQL",
        href: "/catalogue/sql",
        icon: TablePropertiesIcon,
        title: "Catalogue SQL",
        description: "Run read-only SQL and inspect Arrow results.",
      },
      {
        name: "Queries",
        href: "/catalogue/queries",
        icon: FileCode2Icon,
        title: "Saved Queries",
        description: "Author SQL with immutable revision history.",
      },
      {
        name: "Views",
        href: "/catalogue/views",
        icon: ViewIcon,
        title: "Catalogue Views",
        description: "Inspect and edit persistent DuckLake views.",
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
        description: "Compose crawl nodes with SQL-defined edges.",
      },
      {
        name: "Metrics",
        href: "/crawls/metrics",
        icon: ChartNoAxesCombinedIcon,
        title: "Crawl Metrics",
        description: "Inspect graph-run throughput, progress, and failures.",
      },
    ],
  },
  {
    name: "Cache",
    slug: "cache",
    items: [
      {
        name: "Data Schemas",
        href: "/cache/data-schemas",
        icon: BracesIcon,
        title: "Data Schema Registry",
        description:
          "Inspect reusable data schemas, matches, failures, and provenance.",
      },
      {
        name: "Query Schemas",
        href: "/cache/query-schemas",
        icon: ListFilterIcon,
        title: "Query Schema Registry",
        description: "Inspect reusable query parameter data schemas.",
      },
    ],
  },
  {
    name: "Settings",
    slug: "settings",
    items: [
      {
        name: "Crawl Policies",
        href: "/settings/crawl-policies",
        icon: ShieldCheckIcon,
        title: "Crawl Policies",
        description:
          "Inspect, edit, invalidate, and delete crawl transport policies.",
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
  for (const href of [
    "/catalogue/queries",
    "/catalogue/views",
  ]) {
    if (pathname.startsWith(`${href}/`)) {
      return navigationGroups
        .flatMap((group) => group.items)
        .find((item) => item.href === href)
    }
  }
  if (pathname.startsWith("/cache/data-schemas/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/cache/data-schemas")
  }
  if (pathname.startsWith("/cache/query-schemas/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/cache/query-schemas")
  }
  if (pathname.startsWith("/settings/crawl-policies/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/settings/crawl-policies")
  }
  return navigationGroups
    .flatMap((group) => group.items)
    .find((item) => item.href === pathname)
}
