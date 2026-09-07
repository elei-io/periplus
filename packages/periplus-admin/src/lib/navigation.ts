import {
  ChartNoAxesCombinedIcon,
  ShieldCheckIcon,
  Globe2Icon,
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
        description: "Query the public web and DOM catalogue.",
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
        description: "Backfill or rebuild fixed Periplus projections.",
      },
    ],
  },
  {
    name: "Crawler",
    slug: "crawler",
    items: [
      {
        name: "Controls",
        href: "/frontier",
        icon: Globe2Icon,
        title: "Crawler Controls",
        description:
          "Control shared crawler pace, budgets, and background exploration.",
      },
      {
        name: "Collections",
        href: "/collections",
        icon: FileTextIcon,
        title: "Collections",
        description:
          "Inspect collection intent, progress, outcomes, and history.",
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
    ],
  },
]

export const defaultNavigationItem = navigationGroups[1].items[0]
export const homeNavigationItem = {
  name: "Overview",
  href: "/",
  icon: DatabaseZapIcon,
  title: "Periplus Admin",
  description: "Control the continuous crawler and maintain the catalogue.",
}

export function findNavigationItem(pathname: string) {
  if (/^\/frontier\/items\/[^/]+$/.test(pathname)) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/frontier")
  }
  if (pathname === "/") {
    return homeNavigationItem
  }
  if (/^\/collections\/[^/]+$/.test(pathname)) {
    return navigationGroups
      .flatMap((group) => group.items)
      .find((item) => item.href === "/collections")
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
