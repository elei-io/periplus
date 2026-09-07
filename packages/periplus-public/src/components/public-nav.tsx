"use client"

import Link from "next/link"
import { ThemeToggle } from "@/components/theme-toggle"
import { usePathname } from "next/navigation"
import { Compass, ScanSearch, Globe2, BookOpen, Database, PlusCircle, FileCode2 } from "lucide-react"

const destinations = [
  { href: "/discover", label: "Discover", icon: ScanSearch },
  { href: "/datasets", label: "Datasets", icon: Database },
  { href: "/coverage", label: "Coverage", icon: Globe2 },
  { href: "/live", label: "Live", icon: Globe2 },
  { href: "/docs", label: "Docs", icon: FileCode2 },
  { href: "/about", label: "About", icon: BookOpen },
  { href: "/suggest", label: "Suggest", icon: PlusCircle },
]

export function PublicNav() {
  const pathname = usePathname()
  return <header className="public-header">
    <div className="public-header-inner">
      <Link href="/" className="wordmark" aria-label="Periplus home"><Compass aria-hidden="true" />periplus<span className="preview-label">Preview</span></Link>
      <nav className="workspace-nav flex-wrap max-sm:grid! max-sm:grid-cols-4 max-[380px]:grid-cols-3" aria-label="Main navigation">{destinations.map(({ href, label, icon: Icon }) => <Link key={href} href={href} aria-current={pathname === href || pathname.startsWith(`${href}/`) ? "page" : undefined}><Icon aria-hidden="true" />{label}</Link>)}</nav>
      <div className="nav-actions"><ThemeToggle /></div>
    </div>
  </header>
}
