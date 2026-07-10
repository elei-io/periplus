import { useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"

import { IndexProgress } from "@/components/index-run/index-progress"
import { CrawlForm } from "@/components/crawl-run/crawl-form"
import { CatalogueRunSummary } from "@/components/catalogue-run-summary"
import { useCrawlRun } from "@/hooks/use-crawl-run"
import { cn } from "@/lib/utils"

export function CrawlPage() {
  const crawlRun = useCrawlRun()
  const [runKey, setRunKey] = useState(0)
  const initialUrl = useMemo(() => {
    return new URLSearchParams(window.location.search).get("url") ?? ""
  }, [])
  const hasProgress = crawlRun.events.length > 0
  const showResults =
    crawlRun.isSuccess && !crawlRun.isRunning && crawlRun.result !== null

  return (
    <div
      className={cn(
        "grid min-h-[calc(100svh-7rem)] w-full items-start gap-8 transition-[padding] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]",
        hasProgress ? "content-start py-8" : "content-start pt-[20svh]"
      )}
    >
      <section className="mx-auto grid w-full max-w-4xl gap-5">
        <div className="grid max-w-[38rem] gap-2 text-left">
          <h1 className="text-2xl font-semibold tracking-normal">
            Start with a page
          </h1>
          <p className="text-sm leading-6 text-muted-foreground md:text-[0.95rem]">
            Acquire raw HTML, Crawl4AI metadata, and quality warnings from a
            one-off page load. Atlas chooses transport from crawl policies.
          </p>
        </div>
        <CrawlForm
          idPrefix="crawl-playground"
          initialUrl={initialUrl}
          isRunning={crawlRun.isRunning}
          onCancel={crawlRun.cancel}
          onSubmit={(input) => {
            setRunKey((currentRunKey) => currentRunKey + 1)
            crawlRun.run(input)
          }}
        />
        <AnimatedSection
          className="mx-auto w-full max-w-4xl"
          hiddenClassName="-translate-y-1 opacity-0"
          show={hasProgress && !showResults}
          transitionClassName="duration-300"
        >
          <IndexProgress events={crawlRun.events} />
        </AnimatedSection>
      </section>

      <AnimatedSection
        key={`results-${runKey}`}
        className="mx-auto w-full max-w-5xl"
        hiddenClassName="translate-y-3 opacity-0"
        show={showResults}
        transitionClassName="duration-500"
      >
        {crawlRun.result ? (
          <CatalogueRunSummary result={crawlRun.result} />
        ) : null}
      </AnimatedSection>
    </div>
  )
}

type AnimatedSectionProps = {
  children: ReactNode
  className: string
  hiddenClassName: string
  show: boolean
  transitionClassName: string
}

function AnimatedSection({
  children,
  className,
  hiddenClassName,
  show,
  transitionClassName,
}: AnimatedSectionProps) {
  const [entered, setEntered] = useState(false)

  useEffect(() => {
    if (!show) {
      return undefined
    }

    const frame = window.requestAnimationFrame(() => {
      setEntered(true)
    })

    return () => window.cancelAnimationFrame(frame)
  }, [show])

  if (!show) {
    return null
  }

  return (
    <div
      className={cn(
        "transition-[opacity,transform] ease-[cubic-bezier(0.22,1,0.36,1)]",
        className,
        transitionClassName,
        entered ? "translate-y-0 opacity-100" : hiddenClassName
      )}
    >
      {children}
    </div>
  )
}
