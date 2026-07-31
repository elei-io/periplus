import assert from "node:assert/strict"
import test from "node:test"

import { commands, type CommandContext } from "./commands.js"
import type { SqlMetadata } from "./types.js"

const metadata: SqlMetadata = {
  catalogue_version: "2.0.0",
  duckdb_version: "v1.5.5",
  catalogue_bytes: 12_345,
  relations: [
    {
      schema_name: "web",
      name: "page",
      kind: "view",
      description:
        "Canonical normalized URL identities observed through visits.",
      columns: [
        {
          name: "url",
          data_type: "VARCHAR",
          nullable: false,
          description: "Normalized URL.",
        },
        {
          name: "hostname",
          data_type: "VARCHAR",
          nullable: true,
          description: "Normalized hostname.",
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
          name: "tag",
          data_type: "VARCHAR",
          nullable: false,
          description: "Normalized local tag name.",
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
      ],
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
          name: "text_content",
          data_type: "VARCHAR",
          nullable: true,
          description: null,
        },
      ],
    },
  ],
}

const context: CommandContext = {
  history: ["SELECT * FROM web.page;"],
  async metadata() {
    return metadata
  },
}

test("the shell exposes the local inspection commands", () => {
  assert.deepEqual(
    commands.all().map((command) => command.name),
    [
      "clear",
      "exit",
      "help",
      "history",
      "tables",
      "macros",
      "describe",
      "completion",
    ]
  )
})

test("help is generated from the command registry", async () => {
  const result = await commands.execute(".help", context)

  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert(result.rows.some((row) => row[0] === ".describe <object>"))
    assert(result.rows.some((row) => row[0] === ".tables"))
  }
})

test("commands and arguments autocomplete from metadata", async () => {
  assert.deepEqual(
    (await commands.complete(".cl", 3, context)).map((item) => item.value),
    [".clear"]
  )
  assert.deepEqual(
    (await commands.complete(".describe web.p", 15, context)).map(
      (item) => item.value
    ),
    ["web.page"]
  )
  assert.deepEqual(
    (await commands.complete(".describe dom.", 14, context)).map(
      (item) => item.value
    ),
    ["dom.elements", "dom.query_selector_all", "dom.text_content"]
  )
})

test("tables includes views and table macro signatures", async () => {
  const result = await commands.execute(".tables", context)

  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert(result.rows.some((row) => row[0] === "web.page"))
    assert(
      result.rows.some(
        (row) =>
          row[0] === "web.page" &&
          row[3] ===
            "Canonical normalized URL identities observed through visits."
      )
    )
    assert(result.rows.some((row) => row[0] === "dom.elements"))
    assert(result.rows.some((row) => row[0] === "dom.text_content"))
    assert(
      result.rows.some(
        (row) =>
          row[0] === "dom.query_selector_all" &&
          String(row[2]).includes("css_selector VARCHAR")
      )
    )
  }
})

test("macros distinguishes table and scalar macros", async () => {
  const macroMetadata: SqlMetadata = {
    ...metadata,
    macros: [
      ...metadata.macros,
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
    ],
  }
  const result = await commands.execute(".macros", {
    ...context,
    async metadata() {
      return macroMetadata
    },
  })

  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert(
      result.rows.some(
        (row) => row[0] === "dom.get_attribute" && row[1] === "scalar"
      )
    )
    assert(
      result.rows.some(
        (row) => row[0] === "dom.query_selector_all" && row[1] === "table"
      )
    )
  }
})

test("describe renders public metadata", async () => {
  const result = await commands.execute(".describe web.page", context)

  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert.deepEqual(result.rows[1], [
      "hostname",
      "VARCHAR",
      "yes",
      "Normalized hostname.",
    ])
    assert.equal(
      result.summary,
      "web.page · view · Canonical normalized URL identities observed through visits."
    )
  }
})

test("describe renders DOM metadata", async () => {
  const result = await commands.execute(".describe dom.elements", context)

  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert.deepEqual(result.rows[1], [
      "tag",
      "VARCHAR",
      "no",
      "Normalized local tag name.",
    ])
    assert.equal(
      result.summary,
      "dom.elements · view · Structural elements projected from immutable HTML content."
    )
  }
})

test("history uses the shared console history", async () => {
  const result = await commands.execute(".history", context)

  assert.equal(result.kind, "table")
  if (result.kind === "table") {
    assert.deepEqual(result.rows, [[1, "SELECT * FROM web.page;"]])
  }
})

test("exit requests that the shell close", async () => {
  assert.deepEqual(await commands.execute(".exit", context), { kind: "exit" })
})
