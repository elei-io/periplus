import {
  BarChart3Icon,
  BoxesIcon,
  BracesIcon,
  CalendarClockIcon,
  ClipboardListIcon,
  DatabaseIcon,
  FileSearchIcon,
  LinkIcon,
  RouteIcon,
  SearchIcon,
  SparklesIcon,
} from "lucide-react"

import type { NavigationGroup } from "@/types/navigation"

export const navigationGroups: NavigationGroup[] = [
  {
    name: "Playground",
    slug: "playground",
    items: [
      {
        name: "Search",
        href: "/playground/search",
        icon: SearchIcon,
        title: "Search Playground",
        description: "Run ad hoc web searches and inspect ranked results.",
      },
      {
        name: "Index",
        href: "/playground/index",
        icon: DatabaseIcon,
        title: "Index Playground",
        description: "Crawl a starting URL and discover linked pages.",
      },
      {
        name: "Paginate",
        href: "/playground/paginate",
        icon: RouteIcon,
        title: "Pagination Playground",
        description: "Learn and test reusable pagination schemas.",
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
        description:
          "Create, schedule, filter, and archive task definitions.",
      },
      {
        name: "Effects",
        href: "/scheduled-work/effects",
        icon: CalendarClockIcon,
      },
      {
        name: "Metrics",
        href: "/scheduled-work/metrics",
        icon: BarChart3Icon,
      },
    ],
  },
  {
    name: "History",
    slug: "history",
    items: [
      {
        name: "URLs",
        href: "/history/urls",
        icon: LinkIcon,
        title: "Known URLs",
        description: "Inspect URLs Atlas has seen across crawls and artifacts.",
      },
      {
        name: "Crawls",
        href: "/history/crawls",
        icon: RouteIcon,
        title: "Crawl History",
        description: "Inspect real remote visits, status codes, timings, and warnings.",
      },
      {
        name: "Artifacts",
        href: "/history/artifacts",
        icon: BoxesIcon,
        title: "Artifact History",
        description: "Browse cached bytes and invalidate reusable artifacts.",
      },
      {
        name: "Extract Schemas",
        href: "/history/extract-schemas",
        icon: BracesIcon,
        title: "Extract Schema Registry",
        description: "Inspect reusable extraction schemas, matches, failures, and provenance.",
      },
      {
        name: "Pagination Schemas",
        href: "/history/pagination-schemas",
        icon: RouteIcon,
        title: "Pagination Schema Registry",
        description: "Inspect reusable pagination selectors, query templates, and failures.",
      },
    ],
  },
]

export const defaultNavigationItem = navigationGroups[0].items[0]

export function findNavigationItem(pathname: string) {
  if (pathname.startsWith("/history/artifacts/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/history/artifacts")
  }
  if (pathname.startsWith("/history/crawls/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/history/crawls")
  }
  if (pathname.startsWith("/history/urls/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/history/urls")
  }
  if (pathname.startsWith("/history/extract-schemas/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/history/extract-schemas")
  }
  if (pathname.startsWith("/history/pagination-schemas/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/history/pagination-schemas")
  }

  return navigationGroups
    .flatMap((group) => group.items)
    .find((item) => item.href === pathname)
}
