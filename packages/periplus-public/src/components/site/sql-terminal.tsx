"use client"

import dynamic from "next/dynamic"

import { Skeleton } from "@/components/ui/skeleton"

const PeriplusWebShell = dynamic(
  () =>
    import("periplus-web-shell").then((module) => module.PeriplusWebShell),
  {
    ssr: false,
    loading: () => <Skeleton className="h-full min-h-80 w-full rounded-none" />,
  }
)

export function SqlTerminal() {
  return <PeriplusWebShell apiBaseUrl="/api" className="h-full" />
}
