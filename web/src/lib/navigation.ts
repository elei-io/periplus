import {
  BracesIcon,
  GitForkIcon,
  DatabaseIcon,
  FileSearchIcon,
  FileCode2Icon,
  FlaskConicalIcon,
  ListFilterIcon,
  SearchIcon,
  ShieldCheckIcon,
  SparklesIcon,
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
    name: "Playground",
    slug: "playground",
    items: [
      {
        name: "Search",
        href: "/playground/search",
        icon: SearchIcon,
        title: "Search Playground",
        description:
          "Try live search providers and inspect results as they arrive.",
      },
      {
        name: "Index",
        href: "/playground/index",
        icon: DatabaseIcon,
        title: "Index Playground",
        description:
          "Discover linked pages and watch Atlas move through a site.",
      },
      {
        name: "Extract",
        href: "/playground/extract",
        icon: SparklesIcon,
        title: "Extract Playground",
        description: "Test structured extraction against live pages.",
      },
      {
        name: "Crawl",
        href: "/playground/crawl",
        icon: FileSearchIcon,
        title: "Crawl Playground",
        description: "Fetch page content and inspect crawl output.",
      },
      {
        name: "Calibrate",
        href: "/playground/calibrate",
        icon: FlaskConicalIcon,
        title: "Crawl Policy Calibration",
        description:
          "Test transport templates and persist crawl policy settings.",
      },
    ],
  },
  {
    name: "Graphs",
    slug: "graphs",
    items: [
      {
        name: "Crawl Graphs",
        href: "/graphs",
        icon: GitForkIcon,
        title: "Crawl Graphs",
        description: "Compose crawl nodes with SQL-defined edges.",
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
