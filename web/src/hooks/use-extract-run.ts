import { useActionRun } from "@/hooks/use-action-run"
import type { ExtractInput, ExtractOutput } from "@/types/extract"

export function useExtractRun() {
  return useActionRun<ExtractInput, ExtractOutput | null>("/extract/", "Extract", null)
}
