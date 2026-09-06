"use client"

import { useState } from "react"
import { Code2, MessageSquare } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { DiscoveryChat } from "@/components/discovery-chat"
import { QueryWorkbench } from "@/components/query-workbench"

export function DiscoveryWorkspace({ initialMode, question, sql, parameters, autoRun }: { initialMode: string; question?: string; sql?: string; parameters?: string; autoRun: boolean }) {
  const [mode, setMode] = useState(initialMode)
  const [sqlDraft, setSqlDraft] = useState({ sql, parameters })
  return <main className="discovery-workspace">
    <header className="workspace-heading"><h1>Discover</h1><span className="eyebrow">Your view of the web</span></header>
    <Tabs value={mode} onValueChange={value => {
      const next = String(value)
      setMode(next)
      const url = new URL(window.location.href)
      url.searchParams.delete("run")
      url.searchParams.set("mode", next)
      window.history.replaceState(window.history.state, "", url)
    }}><TabsList aria-label="Discovery mode"><TabsTrigger value="agent"><MessageSquare />Ask</TabsTrigger><TabsTrigger value="sql"><Code2 />SQL</TabsTrigger></TabsList>
      <TabsContent value="agent" keepMounted><DiscoveryChat initialPrompt={question} autoRun={autoRun && initialMode === "agent"} onOpenSql={value => {
        setSqlDraft({ sql: value, parameters: undefined })
        setMode("sql")
        const url = new URL(window.location.href)
        url.searchParams.delete("run")
        url.searchParams.set("mode", "sql")
        url.searchParams.set("sql", value)
        url.searchParams.delete("parameters")
        window.history.replaceState(window.history.state, "", url)
      }} /></TabsContent>
      <TabsContent value="sql" keepMounted><QueryWorkbench key={JSON.stringify(sqlDraft)} initialSql={sqlDraft.sql} initialParameters={sqlDraft.parameters} autoRun={autoRun && initialMode === "sql"} /></TabsContent>
    </Tabs>
  </main>
}
