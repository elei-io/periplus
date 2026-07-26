import { safeCompletions } from "./completion.js";
import { analyzeSql, type SqlRelation } from "./sql-analysis.js";
import type {
  AtlasApi,
  CatalogueMetadata,
  CatalogueMetadataColumn,
  CatalogueMetadataFunction,
  CompletionItem,
} from "./types.js";

const keywords = [
  "SELECT",
  "FROM",
  "WHERE",
  "JOIN",
  "LEFT JOIN",
  "RIGHT JOIN",
  "INNER JOIN",
  "ON",
  "AS",
  "GROUP BY",
  "ORDER BY",
  "HAVING",
  "LIMIT",
  "OFFSET",
  "WITH",
  "UNION ALL",
] as const;

export class SqlCompleter {
  private metadata?: Promise<CatalogueMetadata>;
  private metadataFetchedAt = 0;

  constructor(
    private readonly api: AtlasApi,
    private readonly metadataTtlMilliseconds = 30_000,
  ) {}

  async complete(
    input: string,
    cursor: number,
    signal: AbortSignal,
  ): Promise<CompletionItem[]> {
    const beforeCursor = input.slice(0, cursor);
    if (beforeCursor.trimEnd().endsWith(";")) return [];
    const metadata = await this.loadMetadata(signal).catch(() => undefined);
    if (!metadata) return keywordCompletions(input, cursor);

    const context = analyzeSql(input, cursor, metadata);
    if (context.silent) return [];
    const { target } = context;
    const prefix = target.prefix.toLocaleLowerCase();
    const items: CompletionItem[] = [];
    const qualifier = target.segments.length > 1
      ? target.segments.at(-2)
      : undefined;

    if (context.expectsRelation) {
      items.push(
        ...schemaCompletions(metadata, prefix, target.replaceStart, cursor),
        ...relationCompletions(metadata, context.ctes, target, cursor),
        ...tableMacroCompletions(metadata, target, cursor),
      );
    } else if (qualifier) {
      const relation = context.relations.find(
        (candidate) =>
          candidate.alias.toLocaleLowerCase() === qualifier.toLocaleLowerCase(),
      );
      if (relation) {
        items.push(
          ...columnCompletions(
            [relation],
            target,
            cursor,
            context.functionCall?.definition,
            context.functionCall?.argumentIndex,
          ),
        );
      }
    } else if (
      target.prefix ||
      context.functionCall ||
      ["SELECT", "WHERE", "ON", "GROUP", "ORDER", "HAVING"]
        .includes(context.clause ?? "")
    ) {
      items.push(
        ...columnCompletions(
          context.relations,
          target,
          cursor,
          context.functionCall?.definition,
          context.functionCall?.argumentIndex,
        ),
        ...functionCompletions(metadata, target, cursor),
      );
    }

    if (target.segments.length <= 1) {
      items.push(...keywordCompletions(input, cursor, target.replaceStart));
    }
    return safeCompletions(items);
  }

  invalidate(): void {
    this.metadata = undefined;
    this.metadataFetchedAt = 0;
  }

  async reload(
    signal = new AbortController().signal,
  ): Promise<CatalogueMetadata> {
    this.invalidate();
    return this.loadMetadata(signal);
  }

  private loadMetadata(signal: AbortSignal): Promise<CatalogueMetadata> {
    if (
      this.metadata &&
      this.metadataFetchedAt > 0 &&
      Date.now() - this.metadataFetchedAt >= this.metadataTtlMilliseconds
    ) {
      this.invalidate();
    }
    this.metadata ??= this.api.catalogue.metadata(signal)
      .then((metadata) => {
        this.metadataFetchedAt = Date.now();
        return metadata;
      })
      .catch((reason) => {
        this.invalidate();
        throw reason;
      });
    return this.metadata;
  }
}

function columnCompletions(
  relations: SqlRelation[],
  target: ReturnType<typeof analyzeSql>["target"],
  cursor: number,
  calledFunction?: CatalogueMetadataFunction,
  argumentIndex?: number,
): CompletionItem[] {
  const prefix = target.prefix.toLocaleLowerCase();
  const qualifier = target.segments.length > 1
    ? target.segments.at(-2)
    : undefined;
  const duplicates = new Map<string, number>();
  for (const relation of relations) {
    for (const column of relation.columns) {
      const key = column.name.toLocaleLowerCase();
      duplicates.set(key, (duplicates.get(key) ?? 0) + 1);
    }
  }
  return relations.flatMap((relation) =>
    relation.columns
      .filter((column) =>
        column.name.toLocaleLowerCase().startsWith(prefix),
      )
      .map((column) => {
        const qualify = Boolean(qualifier) ||
          (duplicates.get(column.name.toLocaleLowerCase()) ?? 0) > 1;
        const segments = qualify
          ? [qualifier ?? relation.alias, column.name]
          : [column.name];
        return {
          insertText: segments
            .map((segment) => formatIdentifier(segment, target.quoted))
            .join("."),
          replaceStart: target.replaceStart,
          replaceEnd: cursor,
          kind: "column" as const,
          description: columnDescription(
            relation,
            column,
            calledFunction,
            argumentIndex,
          ),
          priority: qualifier ? 120 : 100,
        };
      }),
  );
}

function relationCompletions(
  metadata: CatalogueMetadata,
  ctes: SqlRelation[],
  target: ReturnType<typeof analyzeSql>["target"],
  cursor: number,
): CompletionItem[] {
  const prefix = target.prefix.toLocaleLowerCase();
  const schema = target.segments.length > 1
    ? target.segments.at(-2)?.toLocaleLowerCase()
    : undefined;
  const stored = metadata.relations
    .filter(
      (relation) =>
        (!schema || relation.schema_name.toLocaleLowerCase() === schema) &&
        relation.name.toLocaleLowerCase().startsWith(prefix),
    )
    .map((relation) => ({
      insertText: (
        schema
          ? [relation.schema_name, relation.name]
          : relation.schema_name === metadata.default_schema
            ? [relation.name]
            : [relation.schema_name, relation.name]
      ).map((segment) => formatIdentifier(segment, target.quoted)).join("."),
      replaceStart: target.replaceStart,
      replaceEnd: cursor,
      kind: "relation" as const,
      description: `${relation.kind} · ${relation.columns.length} columns`,
      priority: 90,
    }));
  const common = ctes
    .filter((cte) => cte.name.toLocaleLowerCase().startsWith(prefix))
    .map((cte) => ({
      insertText: formatIdentifier(cte.name, target.quoted),
      replaceStart: target.replaceStart,
      replaceEnd: cursor,
      kind: "relation" as const,
      description: `CTE · ${cte.columns.length} columns`,
      priority: 110,
    }));
  return [...common, ...stored];
}

function tableMacroCompletions(
  metadata: CatalogueMetadata,
  target: ReturnType<typeof analyzeSql>["target"],
  cursor: number,
): CompletionItem[] {
  const prefix = target.prefix.toLocaleLowerCase();
  const schema = target.segments.length > 1
    ? target.segments.at(-2)?.toLocaleLowerCase()
    : undefined;
  return metadata.functions
    .filter(
      (definition) =>
        definition.kind.includes("table") &&
        (!schema ||
          definition.schema_name.toLocaleLowerCase() === schema) &&
        definition.name.toLocaleLowerCase().startsWith(prefix),
    )
    .map((definition) => {
      const segments =
        schema || definition.schema_name !== metadata.default_schema
          ? [definition.schema_name, definition.name]
          : [definition.name];
      return {
        insertText:
          `${segments.map((segment) =>
            formatIdentifier(segment, target.quoted)
          ).join(".")}(`,
        replaceStart: target.replaceStart,
        replaceEnd: cursor,
        kind: "function" as const,
        description: functionDescription(definition),
        priority: 85,
      };
    });
}

function functionCompletions(
  metadata: CatalogueMetadata,
  target: ReturnType<typeof analyzeSql>["target"],
  cursor: number,
): CompletionItem[] {
  const prefix = target.prefix.toLocaleLowerCase();
  return metadata.functions
    .filter(
      (definition) =>
        !definition.kind.includes("table") &&
        definition.name.toLocaleLowerCase().startsWith(prefix),
    )
    .map((definition) => ({
      insertText: `${formatIdentifier(definition.name, target.quoted)}(`,
      replaceStart: target.replaceStart,
      replaceEnd: cursor,
      kind: "function" as const,
      description: functionDescription(definition),
      priority: 50,
    }));
}

function schemaCompletions(
  metadata: CatalogueMetadata,
  prefix: string,
  replaceStart: number,
  cursor: number,
): CompletionItem[] {
  return [...new Set([
    ...metadata.relations.map((item) => item.schema_name),
    ...metadata.functions.map((item) => item.schema_name),
  ])]
    .filter((schema) => schema.toLocaleLowerCase().startsWith(prefix))
    .map((schema) => ({
      insertText: `${formatIdentifier(schema)}.`,
      replaceStart,
      replaceEnd: cursor,
      kind: "schema" as const,
      description: "schema",
      priority: 80,
    }));
}

function keywordCompletions(
  input: string,
  cursor: number,
  knownReplaceStart?: number,
): CompletionItem[] {
  const beforeCursor = input.slice(0, cursor);
  const match = /[A-Za-z_][A-Za-z0-9_]*$/.exec(beforeCursor);
  const prefix = match?.[0] ?? "";
  if (!prefix) return [];
  const replaceStart = knownReplaceStart ?? match?.index ?? cursor;
  return keywords
    .filter((keyword) =>
      keyword.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase()),
    )
    .map((keyword) => ({
      insertText: keyword,
      replaceStart,
      replaceEnd: cursor,
      kind: "keyword",
      description: "SQL keyword",
      priority: 20,
    }));
}

function columnDescription(
  relation: SqlRelation,
  column: CatalogueMetadataColumn,
  calledFunction?: CatalogueMetadataFunction,
  argumentIndex?: number,
): string {
  const parameter = argumentIndex === undefined
    ? undefined
    : calledFunction?.parameters[argumentIndex];
  const expected = parameter
    ? ` · ${parameter.name}: ${parameter.data_type ?? "ANY"}`
    : calledFunction?.varargs
      ? ` · argument: ${calledFunction.varargs}`
      : "";
  return `${relation.alias} · ${column.data_type}` +
    `${column.nullable ? " nullable" : ""}${expected}`;
}

function functionDescription(definition: CatalogueMetadataFunction): string {
  const parameters = definition.parameters
    .map(
      (parameter) =>
        `${parameter.name}${parameter.data_type ? ` ${parameter.data_type}` : ""}`,
    );
  if (definition.varargs) parameters.push(`… ${definition.varargs}`);
  const signature = `${definition.name}(${parameters.join(", ")})`;
  const result = definition.result_columns.length
    ? definition.result_columns
      .map((column) => `${column.name} ${column.data_type}`)
      .join(", ")
    : definition.return_type;
  return [
    signature,
    result ? `→ ${result}` : undefined,
    definition.description ?? undefined,
  ].filter(Boolean).join(" · ");
}

function formatIdentifier(value: string, forceQuote = false): string {
  if (!forceQuote && /^[A-Za-z_][A-Za-z0-9_$]*$/.test(value)) return value;
  return `"${value.replaceAll('"', '""')}"`;
}
