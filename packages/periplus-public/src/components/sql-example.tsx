"use client"

import dynamic from "next/dynamic"

const SqlEditor = dynamic(() => import("@/components/sql-editor").then(module => module.SqlEditor))

export function SqlExample({ sql }: { sql: string }) {
  return <SqlEditor value={sql} readOnly />
}
