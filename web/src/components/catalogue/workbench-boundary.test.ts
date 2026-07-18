import assert from "node:assert/strict"
import { readdirSync, readFileSync } from "node:fs"
import { dirname, relative, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) return sourceFiles(path)
    return /\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".test.ts")
      ? [path]
      : []
  })
}

function importers(fragment: string): string[] {
  return sourceFiles(sourceRoot)
    .filter((path) => readFileSync(path, "utf8").includes(fragment))
    .map((path) => relative(sourceRoot, path))
    .sort()
}

test("DuckDB-Wasm is confined to the catalogue workbench", () => {
  assert.deepEqual(importers("@duckdb/duckdb-wasm"), [
    "components/catalogue/workbench-query-runtime.ts",
  ])
  assert.deepEqual(importers("workbench-query-runtime"), [
    "hooks/use-catalogue-metadata.ts",
    "hooks/use-catalogue-query.ts",
    "hooks/use-catalogue-status.ts",
  ])
  assert.deepEqual(importers("use-catalogue-metadata"), [
    "pages/catalogue/workbench-page.tsx",
  ])
  assert.deepEqual(importers("use-catalogue-query"), [
    "pages/catalogue/workbench-page.tsx",
  ])
  assert.deepEqual(importers("use-catalogue-status"), [
    "components/catalogue/workbench-commands.ts",
    "pages/catalogue/workbench-page.tsx",
  ])
})
