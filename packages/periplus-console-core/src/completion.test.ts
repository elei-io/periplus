import assert from "node:assert/strict"
import test from "node:test"

import { SqlCompleter } from "./completion.js"
import type { SqlMetadata } from "./types.js"

const metadata: SqlMetadata = {
  catalogue_version: "1.0.0",
  duckdb_version: "v1.5.5",
  catalogue_bytes: 12_345,
  relations: [
    {
      schema_name: "web",
      name: "observation",
      kind: "view",
      description: "Terminal URL observations with optional retained content evidence.",
      columns: [
        {
          name: "observation_id",
          data_type: "UUID",
          nullable: false,
          description: "Unique identity of this terminal observation.",
        },
        {
          name: "requested_url",
          data_type: "VARCHAR",
          nullable: false,
          description: "Exact URL Periplus attempted to visit.",
        },
        {
          name: "content_id",
          data_type: "VARCHAR",
          nullable: true,
          description: "Retained immutable content identity, when present.",
        },
      ],
    },
    {
      schema_name: "web",
      name: "link_occurrence",
      kind: "view",
      description: "Observed HTML anchor occurrences resolved in observation context.",
      columns: [
        {
          name: "observation_id",
          data_type: "UUID",
          nullable: false,
          description: "Observation in which the anchor was resolved.",
        },
        {
          name: "target_url",
          data_type: "VARCHAR",
          nullable: false,
          description: "Normalized resolved HTTP target.",
        },
      ],
    },
    {
      schema_name: "content",
      name: "object",
      kind: "view",
      description: "Immutable retained byte sequences keyed by SHA-256.",
      columns: [
        {
          name: "content_id",
          data_type: "VARCHAR",
          nullable: false,
          description: "SHA-256 identity of the logical bytes.",
        },
      ],
    },
    {
      schema_name: "content",
      name: "html_element",
      kind: "view",
      description: "Deterministic HTML5 elements keyed by immutable content.",
      columns: [
        {
          name: "content_id",
          data_type: "VARCHAR",
          nullable: false,
          description: "Immutable HTML content identity.",
        },
        {
          name: "tag",
          data_type: "VARCHAR",
          nullable: false,
          description: "Normalized local tag name.",
        },
      ],
    },
  ],
  macros: [],
}

test("completes qualified evidence relations", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT * FROM web.o")
  const observation = values.find((item) => item.value === "web.observation")

  assert(observation)
  assert.match(observation.description ?? "", /Terminal URL observations/)
})

test("completes columns from aliased observation relations", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete(
    "SELECT o.req FROM web.observation AS o",
    "SELECT o.req".length,
  )

  assert(values.some((item) => item.value === "o.requested_url"))
  assert(!values.some((item) => item.value === "target_url"))
})

test("completes content relations in relation position", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT * FROM content.")

  assert(values.some((item) => item.value === "content.object"))
  assert(values.some((item) => item.value === "content.html_element"))
  assert(!values.some((item) => item.value === "web.observation"))
})

test("completes both public schemas", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT * FROM ")

  assert(values.some((item) => item.value === "web."))
  assert(values.some((item) => item.value === "content."))
})

test("completes columns from aliased HTML element relations", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete(
    "SELECT e.ta FROM content.html_element AS e",
    "SELECT e.ta".length,
  )

  assert(values.some((item) => item.value === "e.tag"))
})

test("does not invent public macros", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT content.")

  assert(!values.some((item) => item.value.endsWith("(")))
})

test("refreshes cached metadata on request", async () => {
  let loads = 0
  const completer = new SqlCompleter(async () => {
    loads += 1
    return metadata
  })

  await completer.metadata()
  await completer.metadata()
  await completer.metadata(true)

  assert.equal(loads, 2)
})
