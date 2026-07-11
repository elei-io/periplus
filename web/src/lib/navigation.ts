import {
  BracesIcon,
  ClipboardListIcon,
  DatabaseIcon,
  FileSearchIcon,
  FlaskConicalIcon,
  ListFilterIcon,
  SearchIcon,
  ShieldCheckIcon,
  SparklesIcon,
  TablePropertiesIcon,
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
    name: "Scheduled Work",
    slug: "scheduled-work",
    items: [
      {
        name: "Tasks",
        href: "/scheduled-work/tasks",
        icon: ClipboardListIcon,
        title: "Task Admin",
        description: "Create, schedule, filter, and archive task definitions.",
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
