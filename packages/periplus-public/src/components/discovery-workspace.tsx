import Link from "next/link"
import { DiscoveryChat } from "@/components/discovery-chat"
import { Button } from "@/components/ui/button"
import type { DatasetBrief, DatasetMode } from "@/types/answer"

export function DiscoveryWorkspace({ question, autoRun, draft, mode = "discover" }: { question?: string; draft?: DatasetBrief; autoRun: boolean; mode?: DatasetMode }) {
  return <main className="discovery-workspace">
    <header className="workspace-heading flex flex-wrap items-center justify-between gap-4 py-6">
      <div className="flex flex-col gap-2"><h1 className="text-xl font-medium">{mode === "discover" ? "Discover" : "Build a dataset"}</h1><p>{mode === "discover" ? "Find out what the data can tell you." : "Your schema. Real data. Explicit validation."}</p></div>
      <Button variant="ghost" nativeButton={false} render={<Link href={mode === "discover" ? "/build" : "/discover"} target="_blank" rel="noopener noreferrer" />}>{mode === "discover" ? "Have a specification? Build a dataset" : "Still exploring? Discover data"}</Button>
    </header>
    <DiscoveryChat initialDraft={draft} initialPrompt={question} autoRun={autoRun} mode={mode} />
  </main>
}
