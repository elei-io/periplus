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

const SqlConsolePage = lazy(() =>
  import("@/pages/sql-console-page").then((module) => ({
    default: module.SqlConsolePage,
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
const CrawlSchedulesPage = lazy(() =>
  import("@/pages/crawls/schedules-page").then((module) => ({
    default: module.CrawlSchedulesPage,
  }))
)
const CrawlScheduleDetailPage = lazy(() =>
  import("@/pages/crawls/schedules-page").then((module) => ({
    default: module.CrawlScheduleDetailPage,
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
const DomainPoliciesPage = lazy(() =>
  import("@/pages/settings/domain-policies-page").then((module) => ({
    default: module.DomainPoliciesPage,
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
    const handlePopState = () => {
      setPathname(getCurrentPathname())
    }

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
    const target = new URL(href, window.location.origin)
    const targetPathname = target.pathname
    if (href === `${window.location.pathname}${window.location.search}`) {
      return
    }

    window.history.pushState(null, "", href)
    setPathname(targetPathname)
  }, [])

  const page = (() => {
    if (pathname === "/") {
      return <SqlConsolePage />
    }

    if (activeItem.href === "/sql") {
      return <SqlConsolePage />
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

    if (activeItem.href === "/crawls/schedules") {
      const scheduleId = pathname.match(/^\/crawls\/schedules\/([^/]+)$/)?.[1]
      if (scheduleId) {
        return (
          <CrawlScheduleDetailPage
            scheduleId={decodeURIComponent(scheduleId)}
            onNavigate={handleNavigate}
          />
        )
      }
      return <CrawlSchedulesPage onNavigate={handleNavigate} />
    }

    if (activeItem.href === "/crawl-policies") {
      const policyId = pathname.match(/^\/crawl-policies\/([^/]+)$/)?.[1]
      if (policyId) {
        return <CrawlPolicyDetailPage policyId={decodeURIComponent(policyId)} />
      }

      return <CrawlPoliciesPage />
    }

    if (activeItem.href === "/domain-policies") {
      return <DomainPoliciesPage />
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
      <AppSidebar
        pathname={activeItem.href}
        onNavigate={handleNavigate}
      />
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
        <div
          className={
            activeItem.href === "/sql"
              ? "relative min-h-0 min-w-0 flex-1 overflow-hidden"
              : "app-surface relative min-h-0 min-w-0 flex-1 [scrollbar-gutter:stable] overflow-auto overscroll-contain p-4 lg:p-6"
          }
        >
          <div className="app-surface-grain pointer-events-none absolute inset-0" />
          <div
            className={
              activeItem.href === "/sql"
                ? "relative z-10 flex h-full min-h-0 min-w-0"
                : "relative z-10 flex min-h-full min-w-0 pb-10"
            }
          >
            <Suspense fallback={<PageFallback />}>{page}</Suspense>
          </div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}

export default App
