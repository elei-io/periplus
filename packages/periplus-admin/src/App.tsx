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
const MaterializationsPage = lazy(() =>
  import("@/pages/materializations-page").then((module) => ({
    default: module.MaterializationsPage,
  }))
)
const DataMetricsPage = lazy(() =>
  import("@/pages/data/metrics-page").then((module) => ({
    default: module.DataMetricsPage,
  }))
)
const DocumentsPage = lazy(() =>
  import("@/pages/data/documents-page").then((module) => ({
    default: module.DocumentsPage,
  }))
)
const FrontierItemDetail = lazy(() =>
  import("@/components/frontier-items").then((module) => ({
    default: module.FrontierItemDetail,
  }))
)
const FrontierControlsPage = lazy(() =>
  import("@/pages/frontier/controls-page").then((module) => ({
    default: module.FrontierControlsPage,
  }))
)
const CollectionsPage = lazy(() =>
  import("@/pages/collections/collections-page").then((module) => ({
    default: module.CollectionsPage,
  }))
)
const NewCollectionPage = lazy(() =>
  import("@/pages/collections/new-collection-page").then((module) => ({
    default: module.NewCollectionPage,
  }))
)
const CollectionDetailPage = lazy(() =>
  import("@/pages/collections/collection-detail-page").then((module) => ({
    default: module.CollectionDetailPage,
  }))
)
const ContentPoliciesPage = lazy(() =>
  import("@/pages/crawls/content-policies-page").then((module) => ({
    default: module.ContentPoliciesPage,
  }))
)
const ContentPolicyDetailPage = lazy(() =>
  import("@/pages/crawls/content-policy-detail-page").then((module) => ({
    default: module.ContentPolicyDetailPage,
  }))
)
const DomainPoliciesPage = lazy(() =>
  import("@/pages/crawls/domain-policies-page").then((module) => ({
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
  const ActiveItemIcon = activeItem.icon

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
    if (!findNavigationItem(pathname)) {
      return (
        <div className="grid flex-1 place-content-center gap-3 text-center">
          <h1 className="text-xl font-medium">Page not found</h1>
          <a href="/frontier" className="underline">
            Open crawler controls
          </a>
        </div>
      )
    }
    const frontierId = pathname.match(/^\/frontier\/items\/([^/]+)$/)?.[1]
    if (frontierId)
      return <FrontierItemDetail id={decodeURIComponent(frontierId)} />
    if (pathname === "/" || activeItem.href === "/frontier") {
      return <FrontierControlsPage />
    }

    if (activeItem.href === "/sql") {
      return <SqlConsolePage />
    }

    if (activeItem.href === "/materializations") {
      return <MaterializationsPage />
    }

    if (activeItem.href === "/data/metrics") {
      return <DataMetricsPage />
    }

    if (activeItem.href === "/data/documents") {
      return <DocumentsPage />
    }

    if (activeItem.href === "/collections") {
      if (pathname === "/collections/new") return <NewCollectionPage />
      const id = pathname.match(/^\/collections\/([^/]+)$/)?.[1]
      return id ? (
        <CollectionDetailPage id={decodeURIComponent(id)} />
      ) : (
        <CollectionsPage />
      )
    }

    if (activeItem.href === "/content-policies") {
      const policyId = pathname.match(/^\/content-policies\/([^/]+)$/)?.[1]
      if (policyId) {
        return (
          <ContentPolicyDetailPage policyId={decodeURIComponent(policyId)} />
        )
      }

      return <ContentPoliciesPage />
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
      <AppSidebar pathname={activeItem.href} onNavigate={handleNavigate} />
      <SidebarInset className="h-svh min-h-0 overflow-hidden">
        <header className="relative z-10 flex h-14 shrink-0 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur-xl">
          <SidebarTrigger
            aria-label={`Toggle sidebar · ${activeItem.name}`}
            title={`Toggle sidebar · ${activeItem.name}`}
          >
            <ActiveItemIcon />
          </SidebarTrigger>
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
              : "app-surface relative min-h-0 min-w-0 flex-1 overflow-auto p-4 lg:p-6"
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
