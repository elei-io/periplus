import {
  ChartNoAxesCombinedIcon,
  BoxesIcon,
  BracesIcon,
  FlaskConicalIcon,
  GitForkIcon,
  GaugeIcon,
  FileCode2Icon,
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
        name: "Policies",
        href: "/crawl-policies",
        icon: ShieldCheckIcon,
        title: "Policies",
        description:
          "Choose how Atlas crawls each website or path.",
      },
      {
        name: "Profiles",
        href: "/crawl-profiles",
        icon: GaugeIcon,
        title: "Profiles",
        description: "Define reusable ways to fetch and retain pages.",
      },
      {
        name: "Trials",
        href: "/crawl-policies/trials",
        icon: FlaskConicalIcon,
        title: "Trials",
        description:
          "See whether a more capable profile finds better evidence.",
      },
    ],
  },
  {
    name: "Docs",
    slug: "docs",
    items: [
      {
        name: "SQL queries",
        href: "/docs/sql-queries",
        icon: BracesIcon,
        title: "SQL Queries",
        description: "Explore Atlas evidence with catalogue SQL.",
      },
      {
        name: "Crawl Graphs",
        href: "/docs/crawl-graphs",
        icon: GitForkIcon,
        title: "Crawl Graphs",
        description: "Turn page evidence into the next useful crawl.",
      },
      {
        name: "Resources & Scaling",
        href: "/docs/resources-scaling",
        icon: BoxesIcon,
        title: "Resources & Scaling",
        description: "Find the limiting resource before adding capacity.",
      },
    ],
  },
]

export const defaultNavigationItem = navigationGroups[0].items[0]

export function findNavigationItem(pathname: string) {
  if (pathname === "/docs") {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/docs/sql-queries")
  }
  if (pathname.startsWith("/crawls/graphs/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/crawls/graphs")
  }
  for (const href of ["/catalogue/queries", "/catalogue/views"]) {
    if (pathname.startsWith(`${href}/`)) {
      return navigationGroups
        .flatMap((group) => group.items)
        .find((item) => item.href === href)
    }
  }
  if (
    pathname.startsWith("/crawl-policies/") &&
    pathname !== "/crawl-policies/trials"
  ) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/crawl-policies")
  }
  if (pathname.startsWith("/crawl-profiles/")) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/crawl-profiles")
  }
  return navigationGroups
    .flatMap((group) => group.items)
    .find((item) => item.href === pathname)
}
