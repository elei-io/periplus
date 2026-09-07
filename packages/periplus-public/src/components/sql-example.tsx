"use client"

import dynamic from "next/dynamic"

const SqlEditor = dynamic(() => import("@/components/sql-editor").then(module => module.SqlEditor), { ssr: false, loading: () => <div className="sql-loading sql-loading-readonly" role="status">Loading SQL…</div> })

export function SqlExample({ sql }: { sql: string }) {
  return <SqlEditor value={sql} readOnly />
}
