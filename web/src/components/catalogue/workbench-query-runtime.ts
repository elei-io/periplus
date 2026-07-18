import * as duckdb from "@duckdb/duckdb-wasm"
import duckdbEhWasm from "@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url"
import duckdbEhWorker from "@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url"
import duckdbMvpWasm from "@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url"
import duckdbMvpWorker from "@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url"

import { apiErrorFromResponse, apiUrl } from "@/lib/api"
import { formatArrowValue } from "@/lib/catalogue-arrow"
import type {
  CatalogueMetadata,
  CatalogueQueryResult,
  CatalogueQueryRuntime,
  CatalogueStatementKind,
} from "@/types/catalogue"

type QuackRuntime = CatalogueQueryRuntime

type WasmSession = {
  db: duckdb.AsyncDuckDB
  connection: duckdb.AsyncDuckDBConnection
  runtime: QuackRuntime
}

const LOCAL_QUACK_ALIAS = "_atlas_quack"
const bundles: duckdb.DuckDBBundles = {
  mvp: { mainModule: duckdbMvpWasm, mainWorker: duckdbMvpWorker },
  eh: { mainModule: duckdbEhWasm, mainWorker: duckdbEhWorker },
}

let runtimePromise: Promise<CatalogueQueryRuntime> | null = null
let sessionPromise: Promise<WasmSession> | null = null
let operationTail: Promise<void> = Promise.resolve()

export function catalogueQueryRuntime(): Promise<CatalogueQueryRuntime> {
  runtimePromise ??= fetch(apiUrl("/catalogue/query-runtime")).then(
    async (response) => {
      if (!response.ok) throw await apiErrorFromResponse(response)
      return response.json() as Promise<CatalogueQueryRuntime>
    }
  )
  return runtimePromise
}

export async function runQuackCatalogueQuery(
  sql: string,
  statementKind: CatalogueStatementKind
): Promise<CatalogueQueryResult> {
  return serializeOperation(() =>
    executeQuackCatalogueQuery(sql, statementKind)
  )
}

async function executeQuackCatalogueQuery(
  sql: string,
  statementKind: CatalogueStatementKind
): Promise<CatalogueQueryResult> {
  const session = await quackSession()
  const executableSql =
    statementKind === "query"
      ? sql
      : `${statementKind === "explain_analyze" ? "EXPLAIN ANALYZE" : "EXPLAIN"} ${sql}`
  const remote = await remoteSend(session.connection, executableSql)
  try {
    const { reader } = remote
    let columns: string[] = []
    let columnTypes: string[] = []
    const rows: unknown[][] = []
    for await (const batch of reader) {
      if (columns.length === 0) {
        columns = batch.schema.fields.map((field) => field.name)
        columnTypes = batch.schema.fields.map((field) => String(field.type))
      }
      const vectors = batch.schema.fields.map((field, index) => ({
        type: String(field.type),
        vector: batch.getChildAt(index),
      }))
      for (let rowIndex = 0; rowIndex < batch.numRows; rowIndex += 1) {
        rows.push(
          vectors.map(({ type, vector }) =>
            formatArrowValue(type, vector?.get(rowIndex))
          )
        )
      }
    }
    if (columns.length === 0 && reader.schema) {
      columns = reader.schema.fields.map((field) => field.name)
      columnTypes = reader.schema.fields.map((field) => String(field.type))
    }
    return { statementKind, columns, columnTypes, rows }
  } finally {
    await remote.close()
  }
}

export function readQuackCatalogueMetadata(): Promise<CatalogueMetadata> {
  return serializeOperation(readCatalogueMetadata)
}

async function readCatalogueMetadata(): Promise<CatalogueMetadata> {
  const { connection, runtime } = await quackSession()
  const relationRows = await remoteRows(
    connection,
    `
    SELECT table_catalog, table_schema, table_name, table_type
    FROM information_schema.tables
    WHERE table_catalog = ?
      AND table_schema NOT IN ('information_schema', 'pg_catalog')
    ORDER BY table_schema, table_name
    LIMIT 5001
    `,
    [runtime.catalogue_alias]
  )
  if (relationRows.length > 5_000) {
    throw new Error("Catalogue metadata exceeds the limit of 5,000 relations.")
  }
  const columnRows = await remoteRows(
    connection,
    `
    SELECT table_catalog, table_schema, table_name, column_name,
           data_type, is_nullable
    FROM information_schema.columns
    WHERE table_catalog = ?
      AND table_schema NOT IN ('information_schema', 'pg_catalog')
    ORDER BY table_schema, table_name, ordinal_position
    LIMIT 100001
    `,
    [runtime.catalogue_alias]
  )
  if (columnRows.length > 100_000) {
    throw new Error("Catalogue metadata exceeds the limit of 100,000 columns.")
  }
  const functionRows = await remoteRows(
    connection,
    `
    SELECT database_name, schema_name, function_name, function_type,
           coalesce(comment, description), return_type, parameters,
           parameter_types, varargs
    FROM duckdb_functions()
    WHERE function_type IN ('scalar', 'aggregate', 'table', 'macro', 'table_macro')
    ORDER BY database_name, schema_name, function_name, function_type, parameters
    LIMIT 5001
    `
  )
  if (functionRows.length > 5_000) {
    throw new Error("Catalogue metadata exceeds the limit of 5,000 functions.")
  }

  const columnsByRelation = new Map<
    string,
    Array<{ name: string; data_type: string; nullable: boolean }>
  >()
  for (const row of columnRows) {
    const key = `${String(row[0])}\0${String(row[1])}\0${String(row[2])}`
    const columns = columnsByRelation.get(key) ?? []
    columns.push({
      name: String(row[3]),
      data_type: String(row[4]),
      nullable: String(row[5]).toUpperCase() === "YES",
    })
    columnsByRelation.set(key, columns)
  }

  const functions = []
  for (const row of functionRows) {
    const names = arrayValues(row[6])
    const types = arrayValues(row[7])
    functions.push({
      catalog_name: String(row[0]),
      schema_name: String(row[1]),
      name: String(row[2]),
      kind: String(row[3]),
      description: row[4] == null ? null : String(row[4]),
      return_type: row[5] == null ? null : String(row[5]),
      parameters: names.map((name, index) => ({
        name,
        data_type: types[index] ?? null,
      })),
      varargs: row[8] == null ? null : String(row[8]),
      result_columns: await tableMacroResultColumns(connection, runtime, row),
    })
  }

  return {
    catalog_name: runtime.catalogue_alias,
    default_schema: runtime.catalogue_schema,
    relations: relationRows.map((row) => ({
      catalog_name: String(row[0]),
      schema_name: String(row[1]),
      name: String(row[2]),
      kind: String(row[3]).toUpperCase() === "VIEW" ? "view" : "table",
      columns:
        columnsByRelation.get(
          `${String(row[0])}\0${String(row[1])}\0${String(row[2])}`
        ) ?? [],
    })),
    functions,
  }
}

export function readQuackCatalogueStatus() {
  return serializeOperation(readCatalogueStatus)
}

async function readCatalogueStatus() {
  const { connection, runtime } = await quackSession()
  const metadata = quoteIdentifier(
    `__ducklake_metadata_${runtime.catalogue_alias}`
  )
  const metadataSchema = quoteIdentifier(runtime.metadata_schema)
  const rows = await remoteRows(
    connection,
    `
    SELECT count(*), coalesce(sum(data_file.file_size_bytes), 0)
    FROM ${metadata}.${metadataSchema}.ducklake_data_file AS data_file
    JOIN ${metadata}.${metadataSchema}.ducklake_table AS table_info
      ON table_info.table_id = data_file.table_id
    JOIN ${metadata}.${metadataSchema}.ducklake_schema AS schema_info
      ON schema_info.schema_id = table_info.schema_id
    WHERE data_file.end_snapshot IS NULL
      AND table_info.end_snapshot IS NULL
      AND schema_info.end_snapshot IS NULL
      AND schema_info.schema_name IN (?, '_atlas', '_atlas_materializations')
    `,
    [runtime.catalogue_schema]
  )
  const versions = await remoteRows(
    connection,
    `
    SELECT extension_version
    FROM duckdb_extensions()
    WHERE extension_name = 'ducklake' AND installed
    `
  )
  return {
    active_file_count: Number(rows[0]?.[0] ?? 0),
    active_storage_bytes: Number(rows[0]?.[1] ?? 0),
    ducklake_version: versions[0]?.[0] == null ? null : String(versions[0][0]),
    catalogue_schema_version: runtime.catalogue_schema_version,
  }
}

async function quackSession(): Promise<WasmSession> {
  sessionPromise ??= initializeQuackSession().catch((error) => {
    sessionPromise = null
    throw error
  })
  return sessionPromise
}

async function initializeQuackSession(): Promise<WasmSession> {
  const runtime = await catalogueQueryRuntime()
  const bundle = await duckdb.selectBundle(bundles)
  const worker = new Worker(bundle.mainWorker!)
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker)
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker)
  const connection = await db.connect()
  try {
    await connection.query("INSTALL quack; LOAD quack;")
    await connection.query(
      `ATTACH ${quoteLiteral(runtime.quack_uri)} AS ${quoteIdentifier(
        LOCAL_QUACK_ALIAS
      )} (TYPE quack, TOKEN ${quoteLiteral(runtime.quack_token)})`
    )
    if (!(await remoteCatalogueExists(connection, runtime.catalogue_alias))) {
      for (const statement of runtime.setup_sql) {
        await remoteRows(connection, statement)
      }
      try {
        await remoteRows(connection, runtime.attach_sql)
      } catch (error) {
        // Server catalogues are global. Another browser may have attached the
        // same Atlas catalogue after our check.
        if (
          !(await remoteCatalogueExists(connection, runtime.catalogue_alias))
        ) {
          throw error
        }
      }
    }
    await remoteRows(
      connection,
      `USE ${quoteIdentifier(runtime.catalogue_alias)}.${quoteIdentifier(
        runtime.catalogue_schema
      )}`
    )
    return { db, connection, runtime }
  } catch (error) {
    await connection.close()
    await db.terminate()
    throw error
  }
}

async function remoteCatalogueExists(
  connection: duckdb.AsyncDuckDBConnection,
  alias: string
): Promise<boolean> {
  const rows = await remoteRows(
    connection,
    "SELECT database_name FROM duckdb_databases() WHERE database_name = ?",
    [alias]
  )
  return rows.length > 0
}

async function remoteSend(
  connection: duckdb.AsyncDuckDBConnection,
  sql: string
) {
  const statement = await connection.prepare(
    `FROM quack_query_by_name(${quoteLiteral(LOCAL_QUACK_ALIAS)}, ?)`
  )
  try {
    const reader = await statement.send(sql)
    return {
      reader,
      close: () => statement.close(),
    }
  } catch (error) {
    await statement.close()
    throw error
  }
}

async function remoteRows(
  connection: duckdb.AsyncDuckDBConnection,
  sql: string,
  parameters: unknown[] = []
): Promise<unknown[][]> {
  const statement = await connection.prepare(
    `FROM quack_query_by_name(${quoteLiteral(LOCAL_QUACK_ALIAS)}, ?)`
  )
  try {
    const table = await statement.query(bindInternalSql(sql, parameters))
    return Array.from({ length: table.numRows }, (_, rowIndex) =>
      table.schema.fields.map((_, columnIndex) =>
        table.getChildAt(columnIndex)?.get(rowIndex)
      )
    )
  } finally {
    await statement.close()
  }
}

function bindInternalSql(sql: string, parameters: unknown[]): string {
  let index = 0
  const rendered = sql.replaceAll("?", () => {
    if (index >= parameters.length) {
      throw new Error(
        "Internal catalogue SQL has fewer parameters than placeholders."
      )
    }
    return quoteValue(parameters[index++])
  })
  if (index !== parameters.length) {
    throw new Error(
      "Internal catalogue SQL has more parameters than placeholders."
    )
  }
  return rendered
}

function quoteValue(value: unknown): string {
  if (value == null) return "NULL"
  if (typeof value === "number" || typeof value === "bigint") {
    return String(value)
  }
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE"
  return quoteLiteral(String(value))
}

function arrayValues(value: unknown): string[] {
  if (value == null) return []
  if (Array.isArray(value)) return value.map(String)
  if (typeof value === "object" && Symbol.iterator in value) {
    return Array.from(value as Iterable<unknown>, String)
  }
  return []
}

async function tableMacroResultColumns(
  connection: duckdb.AsyncDuckDBConnection,
  runtime: QuackRuntime,
  row: unknown[]
) {
  if (
    String(row[0]) !== runtime.catalogue_alias ||
    String(row[3]) !== "table_macro"
  ) {
    return []
  }
  const qualifiedName = quoteQualified(
    String(row[0]),
    String(row[1]),
    String(row[2])
  )
  const argumentsSql = arrayValues(row[6])
    .map(() => "NULL")
    .join(", ")
  try {
    const rows = await remoteRows(
      connection,
      `DESCRIBE SELECT * FROM ${qualifiedName}(${argumentsSql})`
    )
    return rows.map((column) => ({
      name: String(column[0]),
      data_type: String(column[1]),
      nullable: String(column[2]).toUpperCase() === "YES",
    }))
  } catch {
    return []
  }
}

function serializeOperation<T>(operation: () => Promise<T>): Promise<T> {
  const result = operationTail.then(operation, operation)
  operationTail = result.then(
    () => undefined,
    () => undefined
  )
  return result
}

function quoteLiteral(value: string): string {
  return `'${value.replaceAll("'", "''")}'`
}

function quoteIdentifier(value: string): string {
  return `"${value.replaceAll('"', '""')}"`
}

function quoteQualified(...values: string[]): string {
  return values.map(quoteIdentifier).join(".")
}
