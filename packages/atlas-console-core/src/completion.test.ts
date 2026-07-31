import assert from "node:assert/strict"
import test from "node:test"

import { SqlCompleter } from "./completion.js"
import type { SqlMetadata } from "./types.js"

const metadata: SqlMetadata = {
  catalogue_version: "2.0.0",
  duckdb_version: "v1.5.5",
  catalogue_bytes: 12_345,
  relations: [
    {
      schema_name: "web",
      name: "visit",
      kind: "view",
      description: "Destinations admitted and observed during crawls.",
      columns: [
        {
          name: "visit_id",
          data_type: "UUID",
          nullable: false,
          description: "Unique visit identity.",
        },
        {
          name: "requested_url",
          data_type: "VARCHAR",
          nullable: false,
          description: "Exact URL Atlas attempted to visit.",
        },
      ],
    },
    {
      schema_name: "dom",
      name: "elements",
      kind: "view",
      description: "Structural elements projected from immutable HTML content.",
      columns: [
        {
          name: "content_id",
          data_type: "VARCHAR",
          nullable: false,
          description: "Immutable content identity.",
        },
        {
          name: "element_index",
          data_type: "INTEGER",
          nullable: false,
          description: "Element position.",
        },
        {
          name: "tag",
          data_type: "VARCHAR",
          nullable: false,
          description: "Normalized local tag name.",
        },
      ],
    },
    {
      schema_name: "web",
      name: "page",
      kind: "view",
      description: "Canonical normalized URL identities observed through visits.",
      columns: [
        {
          name: "page_id",
          data_type: "UUID",
          nullable: false,
          description: "Deterministic page identity.",
        },
        {
          name: "hostname",
          data_type: "VARCHAR",
          nullable: false,
          description: "Normalized hostname.",
        },
      ],
    },
  ],
  macros: [
    {
      schema_name: "dom",
      name: "query_selector_all",
      kind: "table_macro",
      parameters: [
        { name: "selected_content_id", data_type: "VARCHAR" },
        { name: "css_selector", data_type: "VARCHAR" },
      ],
      return_type: null,
      columns: [
        {
          name: "content_id",
          data_type: "VARCHAR",
          nullable: false,
          description: null,
        },
        {
          name: "element_index",
          data_type: "INTEGER",
          nullable: false,
          description: null,
        },
      ],
    },
    {
      schema_name: "dom",
      name: "get_attribute",
      kind: "scalar_macro",
      parameters: [
        {
          name: "element_attributes",
          data_type: "MAP(VARCHAR, VARCHAR)",
        },
        { name: "attribute_name", data_type: "VARCHAR" },
      ],
      return_type: "VARCHAR",
      columns: [],
    },
    {
      schema_name: "dom",
      name: "text_content",
      kind: "table_macro",
      parameters: [
        { name: "selected_content_id", data_type: "VARCHAR" },
        { name: "selected_element_index", data_type: "INTEGER" },
      ],
      return_type: null,
      columns: [
        {
          name: "content_id",
          data_type: "VARCHAR",
          nullable: false,
          description: null,
        },
        {
          name: "element_index",
          data_type: "INTEGER",
          nullable: false,
          description: null,
        },
        {
          name: "text_content",
          data_type: "VARCHAR",
          nullable: true,
          description: null,
        },
      ],
    },
  ],
}

test("completes qualified relations", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT * FROM web.p")
  const pages = values.find((item) => item.value === "web.page")

  assert(pages)
  assert.match(pages.description ?? "", /Canonical normalized URL identities/)
  assert(!values.some((item) => item.value === "pages"))
})

test("completes public table macros with their call delimiter", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT * FROM dom.query_s")
  const macro = values.find((item) => item.value === "dom.query_selector_all(")

  assert(macro)
  assert.match(macro.description ?? "", /css_selector VARCHAR/)
})

test("completes columns from aliased relations in scope", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete(
    "SELECT v.req FROM web.visit AS v",
    "SELECT v.req".length,
  )
  const requestedUrl = values.find(
    (item) => item.value === "v.requested_url",
  )

  assert(requestedUrl)
  assert.match(requestedUrl.description ?? "", /Exact URL Atlas attempted/)
  assert(!values.some((item) => item.value === "hostname"))
})

test("completes DOM scalar macros in expressions", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT dom.get")

  assert(values.some((item) => item.value === "dom.get_attribute("))
})

test("completes public relations and table macros from the web namespace", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT * FROM web.")

  assert(values.some((item) => item.value === "web.page"))
  assert(!values.some((item) => item.value === "dom.query_selector_all("))
  assert(!values.some((item) => item.value === "dom.elements"))
})

test("completes relations and table macros from the DOM namespace", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT * FROM dom.")

  assert(values.some((item) => item.value === "dom.elements"))
  assert(values.some((item) => item.value === "dom.query_selector_all("))
  assert(values.some((item) => item.value === "dom.text_content("))
  assert(!values.some((item) => item.value === "web.page"))
  assert(!values.some((item) => item.value === "dom.get_attribute("))
})

test("completes scalar macros from the DOM expression namespace", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT dom.")

  assert(values.some((item) => item.value === "dom.get_attribute("))
  assert(!values.some((item) => item.value === "dom.elements"))
})

test("completes both public schemas in relation position", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete("SELECT * FROM ")

  assert(values.some((item) => item.value === "web."))
  assert(values.some((item) => item.value === "dom."))
})

test("completes columns from aliased DOM relations in scope", async () => {
  const completer = new SqlCompleter(async () => metadata)
  const values = await completer.complete(
    "SELECT e.ta FROM dom.elements AS e",
    "SELECT e.ta".length,
  )

  assert(values.some((item) => item.value === "e.tag"))
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
