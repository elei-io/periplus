import { useCallback, useEffect, useMemo, useState } from "react"

import { AppSidebar } from "@/components/app-sidebar"
import { ExtractPage } from "@/pages/playground/extract-page"
import { IndexPage } from "@/pages/playground/index-page"
import { CrawlPage } from "@/pages/playground/crawl-page"
import { SearchPage } from "@/pages/playground/search-page"
import { TasksPage } from "@/pages/admin/tasks-page"
import { ArtifactDetailPage } from "@/pages/history/artifact-detail-page"
import { ArtifactsPage } from "@/pages/history/artifacts-page"
import { CrawlDetailPage } from "@/pages/history/crawl-detail-page"
import { CrawlsPage } from "@/pages/history/crawls-page"
import { UrlDetailPage } from "@/pages/history/url-detail-page"
import { UrlsPage } from "@/pages/history/urls-page"
import {
  defaultNavigationItem,
  findNavigationItem,
  navigationGroups,
} from "@/lib/navigation"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"

function getCurrentPathname() {
  return window.location.pathname
}

export function App() {
  const [pathname, setPathname] = useState(getCurrentPathname)

  useEffect(() => {
    const handlePopState = () => setPathname(getCurrentPathname())

    window.addEventListener("popstate", handlePopState)
    return () => window.removeEventListener("popstate", handlePopState)
  }, [])

  const activeItem = useMemo(() => {
    return findNavigationItem(pathname) ?? defaultNavigationItem
  }, [pathname])

  const activeGroup = useMemo(() => {
    return (
      navigationGroups.find((group) =>
        group.items.some((item) => item.href === activeItem.href)
      ) ?? navigationGroups[0]
    )
  }, [activeItem.href])

  const handleNavigate = useCallback(
    (href: string) => {
      if (href === pathname) {
        return
      }

      window.history.pushState(null, "", href)
      setPathname(href)
    },
    [pathname]
  )

  const page = (() => {
    if (activeItem.href === "/playground/index") {
      return <IndexPage />
    }

    if (activeItem.href === "/playground/search") {
      return <SearchPage />
    }

    if (activeItem.href === "/playground/extract") {
      return <ExtractPage />
    }

    if (activeItem.href === "/playground/crawl") {
      return <CrawlPage />
    }

    if (activeItem.href === "/scheduled-work/tasks") {
      return <TasksPage />
    }

    if (activeItem.href === "/history/artifacts") {
      const artifactId = pathname.match(/^\/history\/artifacts\/([^/]+)$/)?.[1]
      if (artifactId) {
        return <ArtifactDetailPage artifactId={decodeURIComponent(artifactId)} />
      }

      return <ArtifactsPage />
    }

    if (activeItem.href === "/history/crawls") {
      const crawlId = pathname.match(/^\/history\/crawls\/([^/]+)$/)?.[1]
      if (crawlId) {
        return <CrawlDetailPage crawlId={decodeURIComponent(crawlId)} />
      }

      return <CrawlsPage />
    }

    if (activeItem.href === "/history/urls") {
      const urlId = pathname.match(/^\/history\/urls\/([^/]+)$/)?.[1]
      if (urlId) {
        return <UrlDetailPage urlId={decodeURIComponent(urlId)} />
      }

      return <UrlsPage />
    }

    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <h1 className="text-2xl font-medium tracking-normal">
          hello world - {activeItem.name}
        </h1>
      </div>
    )
  })()

  return (
    <SidebarProvider>
      <AppSidebar pathname={activeItem.href} onNavigate={handleNavigate} />
      <SidebarInset>
        <header className="relative z-10 flex h-14 shrink-0 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur-xl">
          <SidebarTrigger />
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-sm font-medium">
              {activeItem.title ?? activeItem.name}
            </span>
            <span className="text-xs text-muted-foreground">
              {activeItem.description ?? activeGroup.name}
            </span>
          </div>
        </header>
        <div className="app-surface relative flex flex-1 overflow-hidden p-4 lg:p-6">
          <div className="app-surface-grain pointer-events-none absolute inset-0" />
          <div className="relative z-10 flex w-full">{page}</div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}

export default App
