import type { LucideIcon } from "lucide-react"

export type NavigationItem = {
  name: string
  href: string
  icon: LucideIcon
  title?: string
  description?: string
}

export type NavigationGroup = {
  name: string
  slug: string
  items: NavigationItem[]
}
