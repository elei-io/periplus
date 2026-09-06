import Link from "next/link"

import { Button } from "@/components/ui/button"
import {
  NavigationMenu,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
} from "@/components/ui/navigation-menu"
import { DatabaseIcon, TerminalIcon } from "lucide-react"

const navigationItems = [
  { href: "/domains", label: "Domains" },
  { href: "/pages", label: "Pages" },
  { href: "/crawl", label: "Request crawl" },
  { href: "/docs", label: "Docs" },
] as const

export function SiteHeader() {
  return (
    <header className="border-b bg-background">
      <div className="mx-auto flex min-h-14 max-w-7xl flex-wrap items-center gap-3 px-4 py-2 sm:px-6 lg:px-8">
        <Link
          href="/"
          className="mr-auto inline-flex items-center gap-2 font-medium"
        >
          <DatabaseIcon className="size-4" aria-hidden="true" />
          <span>periplus</span>
        </Link>

        <NavigationMenu className="order-3 w-full max-w-none sm:order-2 sm:w-auto">
          <NavigationMenuList className="justify-start sm:justify-center">
            {navigationItems.map((item) => (
              <NavigationMenuItem key={item.href}>
                <NavigationMenuLink render={<Link href={item.href} />}>
                  {item.label}
                </NavigationMenuLink>
              </NavigationMenuItem>
            ))}
          </NavigationMenuList>
        </NavigationMenu>

        <Button
          className="order-2 sm:order-3"
          nativeButton={false}
          variant="outline"
          render={<Link href="/sql" />}
        >
          <TerminalIcon data-icon="inline-start" />
          Open SQL
        </Button>
      </div>
    </header>
  )
}
