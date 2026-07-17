import {
  ChartNoAxesCombinedIcon,
  BoxesIcon,
  BracesIcon,
  GitForkIcon,
  FileCode2Icon,
  ShieldCheckIcon,
  Globe2Icon,
  SquareTerminalIcon,
  ViewIcon,
} from "lucide-react"

import type { NavigationGroup } from "@/types/navigation"

export const navigationGroups: NavigationGroup[] = [
  {
    name: "Catalogue",
    slug: "catalogue",
    items: [
      {
        name: "Workbench",
        href: "/catalogue/workbench",
        icon: SquareTerminalIcon,
        title: "Catalogue Workbench",
        description:
          "Explore the DuckLake catalogue in an interactive SQL session.",
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
      {
        name: "Macros",
        href: "/catalogue/macros",
        icon: BracesIcon,
        title: "Table Macros",
        description: "Author reusable, parameterized catalogue relations.",
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
  for (const href of [
    "/catalogue/queries",
    "/catalogue/views",
    "/catalogue/macros",
  ]) {
    if (pathname.startsWith(`${href}/`)) {
      return navigationGroups
        .flatMap((group) => group.items)
        .find((item) => item.href === href)
    }
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
