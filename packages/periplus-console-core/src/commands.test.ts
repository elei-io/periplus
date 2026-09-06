import assert from "node:assert/strict"
import test from "node:test"

import { commands, type CommandContext } from "./commands.js"
import type { SqlMetadata } from "./types.js"

const metadata: SqlMetadata = {
  catalogue_version: "1.0.0",
  duckdb_version: "v1.5.5",
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
      columns: [],
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
      columns: [],
    },
  ],
  macros: [],
}

const context: CommandContext = {
  history: ["SELECT * FROM web.observation;"],
  async metadata() {
    return metadata
  },
}

test("the shell exposes the local inspection commands", () => {
  assert.deepEqual(
    commands.all().map((command) => command.name),
    ["clear", "exit", "help", "history", "tables", "macros", "describe", "completion"],
  )
})

test("help is generated from the command registry", async () => {
  const result = await commands.execute(".help", context)
  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert(result.rows.some((row) => row[0] === ".describe <object>"))
  }
})

test("commands autocomplete the four evidence relations", async () => {
  assert.deepEqual(
    (await commands.complete(".describe web.o", 15, context)).map((item) => item.value),
    ["web.observation"],
  )
  assert.deepEqual(
    (await commands.complete(".describe content.", 18, context)).map((item) => item.value),
    ["content.object", "content.html_element"],
  )
})

test("tables lists exactly the four public views", async () => {
  const result = await commands.execute(".tables", context)
  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert.deepEqual(
      result.rows.map((row) => row[0]),
      ["content.html_element", "content.object", "web.link_occurrence", "web.observation"],
    )
    assert.match(result.summary ?? "", /4 public objects · catalogue 1.0.0/)
  }
})

test("macros reports the deliberately empty surface", async () => {
  const result = await commands.execute(".macros", context)
  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert.deepEqual(result.rows, [])
    assert.match(result.summary ?? "", /0 public macros/)
  }
})

test("describe renders observation metadata", async () => {
  const result = await commands.execute(".describe web.observation", context)
  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert.deepEqual(result.rows[1], [
      "content_id",
      "VARCHAR",
      "yes",
      "Retained immutable content identity, when present.",
    ])
    assert.match(result.summary ?? "", /web\.observation · view/)
  }
})

test("describe renders content metadata", async () => {
  const result = await commands.execute(".describe content.object", context)
  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert.deepEqual(result.rows[0], [
      "content_id",
      "VARCHAR",
      "no",
      "SHA-256 identity of the logical bytes.",
    ])
  }
})

test("history uses the shared console history", async () => {
  const result = await commands.execute(".history", context)
  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert.deepEqual(result.rows, [[1, "SELECT * FROM web.observation;"]])
  }
})

test("exit requests that the shell close", async () => {
  assert.deepEqual(await commands.execute(".exit", context), { kind: "exit" })
})
