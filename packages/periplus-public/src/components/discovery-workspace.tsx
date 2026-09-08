import { DiscoveryChat } from "@/components/discovery-chat"

export function DiscoveryWorkspace({ question, autoRun }: { question?: string; autoRun: boolean }) {
  return <main className="discovery-workspace dataset-discovery-page">
    <header className="flex flex-wrap items-baseline justify-between gap-2 py-6"><h1 className="text-xl font-medium">Discover</h1><p className="text-sm text-muted-foreground">Describe a dataset to start building its definition.</p></header>
    <DiscoveryChat initialPrompt={question} autoRun={autoRun} />
  </main>
}
