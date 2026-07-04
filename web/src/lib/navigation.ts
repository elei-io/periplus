import {
  BarChart3Icon,
  BoxesIcon,
  CalendarClockIcon,
  ClipboardListIcon,
  DatabaseIcon,
  FileSearchIcon,
  GaugeIcon,
  ListIcon,
  SearchIcon,
  Settings2Icon,
  SparklesIcon,
  WrenchIcon,
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
        name: "Extract",
        href: "/playground/extract",
        icon: SparklesIcon,
        title: "Extract Playground",
        description: "Test structured extraction against live pages.",
      },
      {
        name: "Scrape",
        href: "/playground/scrape",
        icon: FileSearchIcon,
        title: "Scrape Playground",
        description: "Fetch page content and inspect scrape output.",
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
    name: "Artifacts",
    slug: "artifacts",
    items: [
      {
        name: "Files",
        href: "/artifacts/files",
        icon: BoxesIcon,
      },
      {
        name: "Metrics",
        href: "/artifacts/metrics",
        icon: GaugeIcon,
      },
    ],
  },
  {
    name: "Settings",
    slug: "settings",
    items: [
      {
        name: "Item 1",
        href: "/settings/item-1",
        icon: Settings2Icon,
      },
      {
        name: "Item 2",
        href: "/settings/item-2",
        icon: WrenchIcon,
      },
      {
        name: "Item 3",
        href: "/settings/item-3",
        icon: ListIcon,
      },
    ],
  },
]

export const defaultNavigationItem = navigationGroups[0].items[0]

export function findNavigationItem(pathname: string) {
  return navigationGroups
    .flatMap((group) => group.items)
    .find((item) => item.href === pathname)
}
