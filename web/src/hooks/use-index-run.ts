import { useActionRun } from "@/hooks/use-action-run"
import type { IndexInput, IndexLink } from "@/types/index"

export function useIndexRun() {
  return useActionRun<IndexInput, IndexLink[]>("/index/", "Index", [])
}
