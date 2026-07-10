import { ChevronDownIcon, ChevronUpIcon } from "lucide-react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import type { ReactNode } from "react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import type { TaskRunRecord } from "@/types/tasks"

const recentPreviewLimit = 5

export type PlaygroundRunDensity = "live" | "recent"

type PlaygroundRunStackProps = {
  activeRuns: TaskRunRecord[]
  ariaLabel: string
  recentRuns: TaskRunRecord[]
  renderRun: (run: TaskRunRecord, density: PlaygroundRunDensity) => ReactNode
}

export function PlaygroundRunStack({
  activeRuns,
  ariaLabel,
  recentRuns,
  renderRun,
}: PlaygroundRunStackProps) {
  const reduceMotion = useReducedMotion()
  const [showAllRecent, setShowAllRecent] = useState(false)
  const hasRuns = activeRuns.length > 0 || recentRuns.length > 0
  const visibleRecentRuns = showAllRecent
    ? recentRuns
    : recentRuns.slice(0, recentPreviewLimit)
  const additionalRecentCount = Math.max(
    0,
    recentRuns.length - recentPreviewLimit
  )

  if (!hasRuns) {
    return null
  }

  return (
    <section
      className="overflow-hidden rounded-2xl border bg-card/75 shadow-[0_18px_60px_rgb(0_0_0/0.08)] backdrop-blur-xl"
      aria-label={ariaLabel}
    >
      <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <div className="flex items-center gap-2.5">
          <span className="text-sm font-medium">Live activity</span>
          {activeRuns.length > 0 ? (
            <span className="flex items-center gap-1.5 rounded-full bg-link/10 px-2 py-0.5 text-[11px] font-medium text-link">
              <span className="relative flex size-1.5">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-link opacity-50 motion-reduce:animate-none" />
                <span className="relative inline-flex size-1.5 rounded-full bg-link" />
              </span>
              {activeRuns.length} active
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">All quiet</span>
          )}
        </div>
      </div>

      <div className="relative">
        <AnimatePresence initial={false} mode="popLayout">
          {activeRuns.map((run) => (
            <motion.div
              key={run.id}
              layout={!reduceMotion}
              className="border-b last:border-b-0"
              initial={
                reduceMotion ? false : { opacity: 0, scale: 0.985, y: -10 }
              }
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={
                reduceMotion
                  ? { opacity: 0 }
                  : { opacity: 0, scale: 0.985, y: 8 }
              }
              transition={
                reduceMotion
                  ? { duration: 0 }
                  : { type: "spring", stiffness: 430, damping: 36, mass: 0.75 }
              }
            >
              {renderRun(run, "live")}
            </motion.div>
          ))}

          {recentRuns.length > 0 ? (
            <motion.div
              key="recent-label"
              layout={!reduceMotion}
              className="border-b bg-muted/20 px-4 py-2 text-[11px] font-medium tracking-[0.12em] text-muted-foreground uppercase"
            >
              Recent results
            </motion.div>
          ) : null}

          {visibleRecentRuns.map((run) => (
            <motion.div
              key={run.id}
              layout={!reduceMotion}
              className="border-b last:border-b-0"
              initial={
                reduceMotion ? false : { opacity: 0, scale: 0.985, y: -8 }
              }
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={
                reduceMotion ? { opacity: 0 } : { opacity: 0, height: 0, y: 6 }
              }
              transition={
                reduceMotion
                  ? { duration: 0 }
                  : { type: "spring", stiffness: 430, damping: 36, mass: 0.75 }
              }
            >
              {renderRun(run, "recent")}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {additionalRecentCount > 0 ? (
        <div className="border-t bg-muted/10 p-1.5">
          <Button
            className="h-8 w-full rounded-lg text-muted-foreground hover:text-foreground"
            size="sm"
            variant="ghost"
            onClick={() => setShowAllRecent((current) => !current)}
          >
            {showAllRecent ? (
              <>
                <ChevronUpIcon />
                Show recent five
              </>
            ) : (
              <>
                <ChevronDownIcon />
                Show {additionalRecentCount} more
              </>
            )}
          </Button>
        </div>
      ) : null}
    </section>
  )
}
