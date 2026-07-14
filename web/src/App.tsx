import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react"

import { AppSidebar } from "@/components/app-sidebar"
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

const CatalogueSqlPage = lazy(() =>
  import("@/pages/catalogue/sql-page").then((module) => ({
    default: module.CatalogueSqlPage,
  }))
)
const CatalogueViewsPage = lazy(() =>
  import("@/pages/catalogue/views-page").then((module) => ({
    default: module.CatalogueViewsPage,
  }))
)
const CatalogueQueriesPage = lazy(() =>
  import("@/pages/catalogue/queries-page").then((module) => ({
    default: module.CatalogueQueriesPage,
  }))
)
const CrawlGraphsPage = lazy(() =>
  import("@/pages/admin/graphs-page").then((module) => ({
    default: module.CrawlGraphsPage,
  }))
)
const CrawlGraphDetailPage = lazy(() =>
  import("@/pages/admin/graphs-page").then((module) => ({
    default: module.CrawlGraphDetailPage,
  }))
)
const CrawlMetricsPage = lazy(() =>
  import("@/pages/crawls/metrics-page").then((module) => ({
    default: module.CrawlMetricsPage,
  }))
)
const CrawlPoliciesPage = lazy(() =>
  import("@/pages/settings/crawl-policies-page").then((module) => ({
    default: module.CrawlPoliciesPage,
  }))
)
const CrawlPolicyDetailPage = lazy(() =>
  import("@/pages/settings/crawl-policy-detail-page").then((module) => ({
    default: module.CrawlPolicyDetailPage,
  }))
)
const CrawlProfilesPage = lazy(() =>
  import("@/pages/settings/crawl-profiles-page").then((module) => ({
    default: module.CrawlProfilesPage,
  }))
)
const CrawlProfileDetailPage = lazy(() =>
  import("@/pages/settings/crawl-profiles-page").then((module) => ({
    default: module.CrawlProfileDetailPage,
  }))
)
const PolicyTrialsPage = lazy(() =>
  import("@/pages/settings/policy-trials-page").then((module) => ({
    default: module.PolicyTrialsPage,
  }))
)
const SqlQueriesDocsPage = lazy(() =>
  import("@/pages/docs/docs-page").then((module) => ({
    default: module.SqlQueriesDocsPage,
  }))
)
const CrawlGraphsDocsPage = lazy(() =>
  import("@/pages/docs/docs-page").then((module) => ({
    default: module.CrawlGraphsDocsPage,
  }))
)
const ResourcesScalingDocsPage = lazy(() =>
  import("@/pages/docs/docs-page").then((module) => ({
    default: module.ResourcesScalingDocsPage,
  }))
)

function PageFallback() {
  return (
    <div
      className="flex flex-1 items-center justify-center text-sm text-muted-foreground"
      role="status"
    >
      Loading…
    </div>
  )
}

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

  const handleNavigate = useCallback((href: string) => {
    const targetPathname = new URL(href, window.location.origin).pathname
    if (href === `${window.location.pathname}${window.location.search}`) {
      return
    }

    window.history.pushState(null, "", href)
    setPathname(targetPathname)
  }, [])

  const page = (() => {
    if (activeItem.href === "/catalogue/sql") {
      return <CatalogueSqlPage />
    }

    if (activeItem.href === "/catalogue/views") {
      const viewId = pathname.match(/^\/catalogue\/views\/([^/]+)$/)?.[1]
      return (
        <CatalogueViewsPage
          viewId={viewId ? decodeURIComponent(viewId) : undefined}
        />
      )
    }

    if (activeItem.href === "/catalogue/queries") {
      const queryId = pathname.match(/^\/catalogue\/queries\/([^/]+)$/)?.[1]
      return (
        <CatalogueQueriesPage
          queryId={queryId ? decodeURIComponent(queryId) : undefined}
        />
      )
    }

    if (activeItem.href === "/crawls/graphs") {
      const graphId = pathname.match(/^\/crawls\/graphs\/([^/]+)$/)?.[1]
      if (graphId) {
        return (
          <CrawlGraphDetailPage
            graphId={decodeURIComponent(graphId)}
            onNavigate={handleNavigate}
          />
        )
      }
      return <CrawlGraphsPage onNavigate={handleNavigate} />
    }

    if (activeItem.href === "/crawls/metrics") {
      return <CrawlMetricsPage />
    }

    if (activeItem.href === "/crawl-policies/trials") {
      return <PolicyTrialsPage />
    }

    if (activeItem.href === "/crawl-policies") {
      const policyId = pathname.match(/^\/crawl-policies\/([^/]+)$/)?.[1]
      if (policyId) {
        return <CrawlPolicyDetailPage policyId={decodeURIComponent(policyId)} />
      }

      return <CrawlPoliciesPage />
    }

    if (activeItem.href === "/crawl-profiles") {
      const profileId = pathname.match(/^\/crawl-profiles\/([^/]+)$/)?.[1]
      if (profileId) {
        return <CrawlProfileDetailPage profileId={decodeURIComponent(profileId)} />
      }
      return <CrawlProfilesPage />
    }

    if (activeItem.href === "/docs/sql-queries") {
      return <SqlQueriesDocsPage onNavigate={handleNavigate} />
    }

    if (activeItem.href === "/docs/crawl-graphs") {
      return <CrawlGraphsDocsPage onNavigate={handleNavigate} />
    }

    if (activeItem.href === "/docs/resources-scaling") {
      return <ResourcesScalingDocsPage onNavigate={handleNavigate} />
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
      <SidebarInset className="h-svh min-h-0 overflow-hidden">
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
        <div className="app-surface relative min-h-0 min-w-0 flex-1 overflow-auto overscroll-contain p-4 lg:p-6">
          <div className="app-surface-grain pointer-events-none absolute inset-0" />
          <div className="relative z-10 flex min-h-full min-w-0">
            <Suspense fallback={<PageFallback />}>{page}</Suspense>
          </div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}

export default App
