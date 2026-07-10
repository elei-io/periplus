import {
  useActionRuns,
  useCancelActionRun,
  useSubmitAction,
} from "@/hooks/use-action-runs"
import type { IndexInput } from "@/types/index"

export function useIndexRuns() {
  return useActionRuns("index")
}

export function useSubmitIndex() {
  return useSubmitAction<IndexInput>("index", "/index/")
}

export function useCancelIndexRun() {
  return useCancelActionRun("index")
}
