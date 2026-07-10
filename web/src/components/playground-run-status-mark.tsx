import { CheckIcon, CircleDashedIcon, CircleXIcon } from "lucide-react"
import { motion, useReducedMotion } from "motion/react"
import type { ReactNode } from "react"

import type { TaskRunRecord } from "@/types/tasks"

type PlaygroundRunStatusMarkProps = {
  activeIcon: ReactNode
  status: TaskRunRecord["status"]
}

export function PlaygroundRunStatusMark({
  activeIcon,
  status,
}: PlaygroundRunStatusMarkProps) {
  const reduceMotion = useReducedMotion()

  if (status === "running") {
    return (
      <span className="relative flex size-7 shrink-0 items-center justify-center rounded-full bg-link/10 text-link">
        <span className="absolute inset-0 animate-ping rounded-full border border-link/30 [animation-duration:1.8s] motion-reduce:animate-none" />
        {activeIcon}
      </span>
    )
  }

  if (status === "queued") {
    return (
      <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <CircleDashedIcon className="size-4 animate-spin [animation-duration:3s] motion-reduce:animate-none" />
      </span>
    )
  }

  if (status === "succeeded") {
    return (
      <motion.span
        initial={reduceMotion ? false : { scale: 0.7, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        className="flex size-7 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-500"
      >
        <CheckIcon className="size-3.5" strokeWidth={2.5} />
      </motion.span>
    )
  }

  return (
    <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
      <CircleXIcon className="size-3.5" />
    </span>
  )
}
