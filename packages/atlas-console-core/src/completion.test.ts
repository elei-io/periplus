import assert from "node:assert/strict"
import test from "node:test"

import { SqlCompleter } from "./completion.js"
import type { SqlMetadata } from "./types.js"

const metadata: SqlMetadata = {
  relations: [
    {
      schema_name: "ingest",
      name: "visits",
      kind: "table",
      columns: [
        { name: "visit_id", data_type: "UUID", nullable: false },
        { name: "requested_url", data_type: "VARCHAR", nullable: false },
      ],
    },
    {
      schema_name: "material",
      name: "pages",
      kind: "table",
      columns: [
        { name: "page_id", data_type: "UUID", nullable: false },
        { name: "hostname", data_type: "VARCHAR", nullable: false },
      ],
    },
  ],
}

test("completes qualified relations", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT * FROM mat")

  assert(values.some((item) => item.value === "material.pages"))
})

test("completes columns from relations in scope", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete(
    "SELECT req FROM ingest.visits",
    "SELECT req".length,
  )

  assert(values.some((item) => item.value === "requested_url"))
  assert(!values.some((item) => item.value === "hostname"))
})
