import {
  useActionRuns,
  useCancelActionRun,
  useSubmitAction,
} from "@/hooks/use-action-runs"
import type { ExtractInput } from "@/types/extract"

export function useExtractRuns() {
  return useActionRuns("extract")
}

export function useSubmitExtract() {
  return useSubmitAction<ExtractInput>("extract", "/extract/")
}

export function useCancelExtractRun() {
  return useCancelActionRun("extract")
}
