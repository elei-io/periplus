import Link from "next/link"

import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { docsNavigation } from "@/lib/docs"

export default function DocsLayout({ children }: LayoutProps<"/docs">) {
  return (
    <div className="mx-auto grid w-full max-w-7xl flex-1 gap-8 px-4 py-10 sm:px-6 md:grid-cols-[13rem_1fr] lg:px-8">
      <aside>
        <p className="mb-3 text-xs font-medium">Documentation</p>
        <Separator className="mb-3" />
        <nav className="flex flex-wrap gap-1 md:flex-col" aria-label="Documentation">
          {docsNavigation.map((item) => (
            <Button
              key={item.href}
              className="justify-start"
              nativeButton={false}
              variant="ghost"
              render={<Link href={item.href} />}
            >
              {item.label}
            </Button>
          ))}
        </nav>
      </aside>
      <main className="min-w-0">{children}</main>
    </div>
  )
}
