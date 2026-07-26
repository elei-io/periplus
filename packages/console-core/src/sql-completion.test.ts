import assert from "node:assert/strict";
import test from "node:test";
import { SqlCompleter } from "./sql-completion.js";
import type { AtlasApi, CatalogueMetadata } from "./types.js";

const metadata: CatalogueMetadata = {
  catalog_name: "atlas",
  default_schema: "main",
  relations: [
    {
      catalog_name: "atlas",
      schema_name: "main",
      name: "elements",
      kind: "table",
      columns: [
        { name: "crawl_id", data_type: "UUID", nullable: false },
        { name: "tag_name", data_type: "VARCHAR", nullable: false },
        { name: "Display Name", data_type: "VARCHAR", nullable: true },
      ],
    },
    {
      catalog_name: "atlas",
      schema_name: "main",
      name: "crawls",
      kind: "table",
      columns: [
        { name: "crawl_id", data_type: "UUID", nullable: false },
        { name: "parent_id", data_type: "UUID", nullable: true },
      ],
    },
  ],
  functions: [
    {
      catalog_name: "atlas",
      schema_name: "main",
      name: "count_if",
      kind: "scalar",
      description: "Count rows satisfying a condition",
      return_type: "BIGINT",
      parameters: [{ name: "condition", data_type: "BOOLEAN" }],
      varargs: null,
      result_columns: [],
    },
    {
      catalog_name: "atlas",
      schema_name: "macros",
      name: "page_links",
      kind: "table_macro",
      description: "Return links for one crawl",
      return_type: null,
      parameters: [{ name: "crawl", data_type: "UUID" }],
      varargs: null,
      result_columns: [
        { name: "target_url", data_type: "VARCHAR", nullable: false },
        { name: "relationship", data_type: "VARCHAR", nullable: false },
      ],
    },
  ],
};

function completer() {
  const api = {
    catalogue: {
      async metadata() {
        return metadata;
      },
    },
  } as unknown as AtlasApi;
  return new SqlCompleter(api);
}

async function insertions(sql: string, cursor = sql.length) {
  return (
    await completer().complete(
      sql,
      cursor,
      new AbortController().signal,
    )
  ).map((item) => item.insertText);
}

test("tracks every FROM and JOIN relation and resolves their aliases", async () => {
  const sql =
    "SELECT e.tag_name FROM elements e\n" +
    "JOIN crawls AS c ON e.crawl_id = c.cra";
  assert.deepEqual(await insertions(sql), ["c.crawl_id"]);
});

test("qualifies ambiguous unqualified columns", async () => {
  const sql =
    "SELECT * FROM elements e JOIN crawls c ON e.crawl_id = c.crawl_id " +
    "WHERE cra";
  assert.deepEqual(
    (await insertions(sql)).filter((value) => value.includes("crawl_id")),
    ["c.crawl_id", "e.crawl_id"],
  );
});

test("completes columns throughout expression-bearing SQL clauses", async () => {
  const cases = [
    ["SELECT ta FROM elements e", "ta", "tag_name"],
    ["SELECT * FROM elements e WHERE ta", "ta", "tag_name"],
    ["SELECT * FROM elements e GROUP BY ta", "ta", "tag_name"],
    ["SELECT * FROM elements e ORDER BY ta", "ta", "tag_name"],
    [
      "SELECT * FROM elements e JOIN crawls c ON e.ta = c.parent_id",
      "e.ta",
      "e.tag_name",
    ],
  ] as const;
  for (const [sql, marker, expected] of cases) {
    const cursor = sql.indexOf(marker) + marker.length;
    const items = await completer().complete(
      sql,
      cursor,
      new AbortController().signal,
    );
    assert.ok(
      items.some(
        (item) => item.kind === "column" && item.insertText === expected,
      ),
      `${expected} was not offered for ${sql}`,
    );
  }
});

test("derives CTE output columns and completes the CTE as a relation", async () => {
  const sql =
    "WITH recent AS (\n" +
    "  SELECT e.crawl_id AS id, e.tag_name FROM elements e\n" +
    ")\nSELECT recent.i FROM recent";
  const cursor = sql.indexOf("recent.i") + "recent.i".length;
  assert.deepEqual(await insertions(sql, cursor), ["recent.id"]);
  assert.ok((await insertions("WITH recent AS (SELECT crawl_id FROM crawls) SELECT * FROM rec"))
    .includes("recent"));
});

test("nested queries retain correlated outer aliases", async () => {
  const sql =
    "SELECT * FROM elements e WHERE EXISTS (\n" +
    "  SELECT 1 FROM crawls c WHERE c.parent_id = e.ta\n" +
    ")";
  const cursor = sql.indexOf("e.ta") + "e.ta".length;
  assert.deepEqual(await insertions(sql, cursor), ["e.tag_name"]);
});

test("derived-table aliases expose projected columns", async () => {
  const sql =
    "SELECT recent.i FROM (" +
    "SELECT crawl_id AS id, parent_id FROM crawls" +
    ") AS recent";
  const cursor = sql.indexOf("recent.i") + "recent.i".length;
  assert.deepEqual(await insertions(sql, cursor), ["recent.id"]);
});

test("respects quoted identifiers and preserves quoting", async () => {
  const sql = 'SELECT "e"."Di" FROM "main"."elements" AS "e"';
  const cursor = sql.indexOf('"Di"') + 3;
  assert.deepEqual(await insertions(sql, cursor), [
    '"e"."Display Name"',
  ]);
});

test("completes table macros and their result columns", async () => {
  assert.ok(
    (await insertions("SELECT * FROM page_")).includes(
      "macros.page_links(",
    ),
  );
  assert.ok(
    (await insertions("SELECT * FROM macro")).includes("macros."),
  );
  assert.ok(
    (await insertions("SELECT * FROM macros.")).includes(
      "macros.page_links(",
    ),
  );
  const sql = "SELECT p.tar FROM macros.page_links('crawl') p";
  const cursor = sql.indexOf("p.tar") + "p.tar".length;
  assert.deepEqual(await insertions(sql, cursor), ["p.target_url"]);
});

test("an incomplete table-macro call never blocks completion", async () => {
  const values = await insertions("SELECT * FROM macros.page_links(");

  assert.ok(values.includes("target_url"));
});

test("function argument completion includes parameter and column types", async () => {
  const sql = "SELECT count_if(e.ta) FROM elements e";
  const cursor = sql.indexOf("e.ta") + "e.ta".length;
  const items = await completer().complete(
    sql,
    cursor,
    new AbortController().signal,
  );
  assert.equal(items[0]?.insertText, "e.tag_name");
  assert.match(items[0]?.description ?? "", /condition: BOOLEAN/);
});

test("completion remains silent in strings, comments, and after semicolons", async () => {
  assert.deepEqual(await insertions("SELECT 'from ele"), []);
  assert.deepEqual(await insertions("SELECT 1 -- from ele"), []);
  assert.deepEqual(await insertions("SELECT 1;"), []);
});

test("metadata refreshes after its deterministic TTL", async () => {
  let requests = 0;
  const api = {
    catalogue: {
      async metadata() {
        requests += 1;
        return metadata;
      },
    },
  } as unknown as AtlasApi;
  const sql = new SqlCompleter(api, 0);
  await sql.complete("sel", 3, new AbortController().signal);
  await sql.complete("fro", 3, new AbortController().signal);
  assert.equal(requests, 2);
});
