"use client"

import Link from "next/link"
import { ThemeToggle } from "@/components/theme-toggle"
import { Badge } from "@/components/ui/badge"
import { usePathname } from "next/navigation"
import { Compass, ScanSearch, Globe2, BookOpen, FileCode2 } from "lucide-react"

const destinations = [
  { href: "/sql", label: "SQL", icon: FileCode2 },
  { href: "/discover", label: "Discover", icon: ScanSearch },
  { href: "/coverage", label: "Coverage", icon: Globe2 },
  { href: "/docs", label: "Docs", icon: FileCode2 },
  { href: "/about", label: "About", icon: BookOpen },
]

export function PublicNav() {
  const pathname = usePathname()
  return <header className="public-header">
    <div className="public-header-inner">
      <div className="flex shrink-0 items-start gap-2">
        <Link href="/" className="wordmark" aria-label="Periplus home"><Compass aria-hidden="true" />periplus</Link>
        <Badge variant="outline" className="relative -top-1 border-purple-200 bg-purple-50 text-purple-700 dark:border-purple-400/30 dark:bg-purple-400/10 dark:text-purple-300">Research preview</Badge>
      </div>
      <nav className="workspace-nav flex-wrap max-sm:grid! max-sm:grid-cols-4 max-[380px]:grid-cols-3" aria-label="Main navigation">{destinations.map(({ href, label, icon: Icon }) => <Link key={href} href={href} aria-current={pathname === href || pathname.startsWith(`${href}/`) ? "page" : undefined}><Icon aria-hidden="true" />{label}</Link>)}</nav>
      <div className="nav-actions"><ThemeToggle /></div>
    </div>
  </header>
}
