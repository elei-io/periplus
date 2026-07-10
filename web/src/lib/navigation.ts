import {
  BarChart3Icon,
  BoxesIcon,
  BracesIcon,
  CalendarClockIcon,
  ClipboardListIcon,
  DatabaseIcon,
  FileSearchIcon,
  FlaskConicalIcon,
  LinkIcon,
  ListFilterIcon,
  RouteIcon,
  SearchIcon,
  ShieldCheckIcon,
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
        description:
          "Try live search providers and inspect results as they arrive.",
      },
      {
        name: "Index",
        href: "/playground/index",
        icon: DatabaseIcon,
        title: "Index Playground",
        description: "Crawl a starting URL and discover linked pages.",
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
        description: "Test transport templates and persist crawl policy settings.",
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
        name: "Data Schemas",
        href: "/history/data-schemas",
        icon: BracesIcon,
        title: "Data Schema Registry",
        description: "Inspect reusable data schemas, matches, failures, and provenance.",
      },
      {
        name: "Query Schemas",
        href: "/history/query-schemas",
        icon: ListFilterIcon,
        title: "Query Schema Registry",
        description: "Inspect reusable query parameter data schemas.",
      },
      {
        name: "Crawl Policies",
        href: "/history/crawl-policies",
        icon: ShieldCheckIcon,
        title: "Crawl Policy Registry",
        description: "Inspect, edit, invalidate, and delete crawl transport policies.",
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
  if (pathname.startsWith("/history/data-schemas/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/history/data-schemas")
  }
  if (pathname.startsWith("/history/query-schemas/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/history/query-schemas")
  }
  if (pathname.startsWith("/history/crawl-policies/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/history/crawl-policies")
  }
  return navigationGroups
    .flatMap((group) => group.items)
    .find((item) => item.href === pathname)
}
