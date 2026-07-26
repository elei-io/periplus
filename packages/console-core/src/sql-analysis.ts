import type {
  CatalogueMetadata,
  CatalogueMetadataColumn,
  CatalogueMetadataFunction,
  CatalogueMetadataRelation,
} from "./types.js";

const clauseKeywords = new Set([
  "SELECT",
  "FROM",
  "JOIN",
  "WHERE",
  "ON",
  "GROUP",
  "ORDER",
  "HAVING",
  "LIMIT",
  "QUALIFY",
  "UNION",
]);
const aliasStopKeywords = new Set([
  ...clauseKeywords,
  "LEFT",
  "RIGHT",
  "FULL",
  "INNER",
  "OUTER",
  "CROSS",
  "ASOF",
  "USING",
  "WINDOW",
  "OFFSET",
]);

export interface SqlToken {
  kind: "identifier" | "keyword" | "string" | "punctuation";
  value: string;
  raw: string;
  start: number;
  end: number;
  depth: number;
  quoted?: boolean;
}

export interface SqlRelation {
  name: string;
  schema?: string;
  alias: string;
  columns: CatalogueMetadataColumn[];
  kind: "table" | "view" | "cte" | "subquery" | "table_macro";
  depth: number;
}

export interface SqlCompletionTarget {
  segments: string[];
  prefix: string;
  replaceStart: number;
  quoted: boolean;
}

export interface SqlContext {
  tokens: SqlToken[];
  depth: number;
  clause?: string;
  expectsRelation: boolean;
  target: SqlCompletionTarget;
  relations: SqlRelation[];
  ctes: SqlRelation[];
  functionCall?: {
    definition?: CatalogueMetadataFunction;
    argumentIndex: number;
  };
  silent: boolean;
}

export function analyzeSql(
  sql: string,
  cursor: number,
  metadata: CatalogueMetadata,
): SqlContext {
  const tokens = tokenizeSql(sql);
  const depth = depthAt(sql, cursor);
  const before = tokens.filter((token) => token.start < cursor);
  const target = completionTarget(
    sql,
    cursor,
    tokenizeSql(sql.slice(0, cursor)),
  );
  const ctes = collectCtes(tokens, metadata);
  const relations = collectRelations(tokens, metadata, ctes)
    .filter((relation) => relation.depth <= depth);
  const significant = before.filter(
    (token) => token.kind !== "string" && token.end <= target.replaceStart,
  );
  const clauseToken = [...significant].reverse().find(
    (token) =>
      token.depth === depth &&
      token.kind === "keyword" &&
      clauseKeywords.has(token.value),
  );
  const previous = significant.at(-1);
  const expectsRelation =
    previous?.depth === depth &&
    previous.kind === "keyword" &&
    ["FROM", "JOIN"].includes(previous.value) ||
    isFromComma(significant, depth);

  return {
    tokens,
    depth,
    clause: clauseToken?.value,
    expectsRelation,
    target,
    relations,
    ctes: ctes.filter((cte) => cte.depth <= depth),
    functionCall: findFunctionCall(before, cursor, metadata),
    silent: cursorInsideLiteralOrComment(sql, cursor),
  };
}

export function tokenizeSql(sql: string): SqlToken[] {
  const tokens: SqlToken[] = [];
  let index = 0;
  let depth = 0;
  while (index < sql.length) {
    const start = index;
    const character = sql[index]!;
    if (/\s/.test(character)) {
      index += 1;
      continue;
    }
    if (character === "-" && sql[index + 1] === "-") {
      index = sql.indexOf("\n", index + 2);
      if (index < 0) break;
      continue;
    }
    if (character === "/" && sql[index + 1] === "*") {
      const end = sql.indexOf("*/", index + 2);
      index = end < 0 ? sql.length : end + 2;
      continue;
    }
    if (character === "'") {
      index = scanQuoted(sql, index, "'");
      tokens.push({
        kind: "string",
        value: sql.slice(start, index),
        raw: sql.slice(start, index),
        start,
        end: index,
        depth,
      });
      continue;
    }
    if (character === '"') {
      index = scanQuoted(sql, index, '"');
      const raw = sql.slice(start, index);
      tokens.push({
        kind: "identifier",
        value: unquoteIdentifier(raw),
        raw,
        start,
        end: index,
        depth,
        quoted: true,
      });
      continue;
    }
    if (character === "(") {
      tokens.push({
        kind: "punctuation",
        value: character,
        raw: character,
        start,
        end: ++index,
        depth,
      });
      depth += 1;
      continue;
    }
    if (character === ")") {
      depth = Math.max(0, depth - 1);
      tokens.push({
        kind: "punctuation",
        value: character,
        raw: character,
        start,
        end: ++index,
        depth,
      });
      continue;
    }
    if (/[A-Za-z_]/.test(character)) {
      index += 1;
      while (index < sql.length && /[A-Za-z0-9_$]/.test(sql[index]!)) {
        index += 1;
      }
      const raw = sql.slice(start, index);
      const upper = raw.toUpperCase();
      tokens.push({
        kind: isKeyword(upper) ? "keyword" : "identifier",
        value: isKeyword(upper) ? upper : raw,
        raw,
        start,
        end: index,
        depth,
      });
      continue;
    }
    tokens.push({
      kind: "punctuation",
      value: character,
      raw: character,
      start,
      end: ++index,
      depth,
    });
  }
  return tokens;
}

function collectCtes(
  tokens: SqlToken[],
  metadata: CatalogueMetadata,
): SqlRelation[] {
  const ctes: SqlRelation[] = [];
  for (let index = 0; index < tokens.length; index += 1) {
    if (tokens[index]?.value !== "WITH") continue;
    const depth = tokens[index]!.depth;
    index += 1;
    if (tokens[index]?.value === "RECURSIVE") index += 1;
    while (index < tokens.length) {
      const name = tokens[index];
      if (!isIdentifier(name) || name.depth !== depth) break;
      index += 1;
      let explicitColumns: CatalogueMetadataColumn[] = [];
      if (tokens[index]?.value === "(" && tokens[index]?.depth === depth) {
        const close = matchingClose(tokens, index);
        explicitColumns = tokens.slice(index + 1, close)
          .filter(isIdentifier)
          .map((token) => unknownColumn(token.value));
        index = close + 1;
      }
      if (tokens[index]?.value === "AS") index += 1;
      if (tokens[index]?.value !== "(") break;
      const open = index;
      const close = matchingClose(tokens, open);
      const columns = explicitColumns.length
        ? explicitColumns
        : projectionColumns(tokens.slice(open + 1, close), metadata, ctes);
      ctes.push({
        name: name.value,
        alias: name.value,
        columns,
        kind: "cte",
        depth,
      });
      index = close + 1;
      if (tokens[index]?.value !== ",") break;
      index += 1;
    }
  }
  return ctes;
}

function projectionColumns(
  tokens: SqlToken[],
  metadata: CatalogueMetadata,
  ctes: SqlRelation[],
): CatalogueMetadataColumn[] {
  const select = tokens.findIndex((token) => token.value === "SELECT");
  if (select < 0) return [];
  const depth = tokens[select]!.depth;
  const from = tokens.findIndex(
    (token, index) =>
      index > select && token.depth === depth && token.value === "FROM",
  );
  const projection = tokens.slice(select + 1, from < 0 ? tokens.length : from);
  const groups = splitAtCommas(projection, depth);
  return groups.flatMap((group) => {
    const as = group.findIndex((token) => token.value === "AS");
    const alias = as >= 0 ? group[as + 1] : undefined;
    if (isIdentifier(alias)) return [unknownColumn(alias.value)];
    const last = [...group].reverse().find(isIdentifier);
    if (last) return [unknownColumn(last.value)];
    if (group.some((token) => token.value === "*")) {
      return metadata.relations.flatMap((relation) => relation.columns);
    }
    return [];
  });
}

function collectRelations(
  tokens: SqlToken[],
  metadata: CatalogueMetadata,
  ctes: SqlRelation[],
): SqlRelation[] {
  const relations: SqlRelation[] = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index]!;
    if (!["FROM", "JOIN"].includes(token.value)) continue;
    const parsed = parseRelation(tokens, index + 1, token.depth, metadata, ctes);
    if (parsed) {
      relations.push(parsed.relation);
      if (tokens[index + 1]?.value !== "(") {
        index = Math.max(index, parsed.end - 1);
      }
    }
    if (token.value !== "FROM") continue;
    while (index < tokens.length) {
      const comma = tokens.findIndex(
        (candidate, candidateIndex) =>
          candidateIndex > index &&
          candidate.depth === token.depth &&
          (candidate.value === "," ||
            candidate.kind === "keyword" &&
              clauseKeywords.has(candidate.value)),
      );
      if (comma < 0 || tokens[comma]?.value !== ",") break;
      const additional = parseRelation(
        tokens,
        comma + 1,
        token.depth,
        metadata,
        ctes,
      );
      if (!additional) break;
      relations.push(additional.relation);
      index = additional.end - 1;
    }
  }
  return relations;
}

function parseRelation(
  tokens: SqlToken[],
  start: number,
  depth: number,
  metadata: CatalogueMetadata,
  ctes: SqlRelation[],
): { relation: SqlRelation; end: number } | undefined {
  const first = tokens[start];
  if (first?.value === "(" && first.depth === depth) {
    const close = matchingClose(tokens, start);
    if (close < 0) return undefined;
    let index = close + 1;
    if (tokens[index]?.value === "AS") index += 1;
    const alias = tokens[index];
    if (!isIdentifier(alias) || alias.depth !== depth) return undefined;
    return {
      relation: {
        name: alias.value,
        alias: alias.value,
        columns: projectionColumns(
          tokens.slice(start + 1, close),
          metadata,
          ctes,
        ),
        kind: "subquery",
        depth,
      },
      end: index + 1,
    };
  }
  if (!isIdentifier(first) || first.depth !== depth) return undefined;
  const segments = [first.value];
  let index = start + 1;
  while (
    tokens[index]?.value === "." &&
    isIdentifier(tokens[index + 1])
  ) {
    segments.push(tokens[index + 1]!.value);
    index += 2;
  }
  let columns: CatalogueMetadataColumn[] = [];
  let kind: SqlRelation["kind"] = "table";
  if (tokens[index]?.value === "(") {
    const definition = findFunction(metadata, segments.at(-1)!);
    if (!definition || !definition.kind.includes("table")) return undefined;
    columns = definition.result_columns;
    kind = "table_macro";
    const close = matchingClose(tokens, index);
    index = close < 0 ? tokens.length : close + 1;
  } else {
    const cte = ctes.find(
      (candidate) =>
        candidate.name.toLocaleLowerCase() ===
        segments.at(-1)!.toLocaleLowerCase(),
    );
    const stored = findRelation(metadata, segments);
    if (!cte && !stored) return undefined;
    columns = cte?.columns ?? stored!.columns;
    kind = cte ? "cte" : stored!.kind;
  }
  if (tokens[index]?.value === "AS") index += 1;
  const aliasToken = tokens[index];
  const alias =
    isIdentifier(aliasToken) &&
    aliasToken.depth === depth &&
    !aliasStopKeywords.has(aliasToken.value.toUpperCase())
      ? aliasToken.value
      : segments.at(-1)!;
  if (alias === aliasToken?.value) index += 1;
  return {
    relation: {
      name: segments.at(-1)!,
      schema: segments.length > 1 ? segments.at(-2) : undefined,
      alias,
      columns,
      kind,
      depth,
    },
    end: index,
  };
}

function completionTarget(
  sql: string,
  cursor: number,
  tokens: SqlToken[],
): SqlCompletionTarget {
  const segments: string[] = [];
  let index = tokens.length - 1;
  let replaceStart = cursor;
  let quoted = false;
  const last = tokens[index];
  if (
    last &&
    last.kind === "identifier" &&
    last.end === cursor
  ) {
    segments.unshift(last.value);
    replaceStart = last.start;
    quoted = Boolean(last.quoted);
    index -= 1;
  } else if (last?.value === "." && last.end === cursor) {
    segments.unshift("");
    replaceStart = cursor;
  } else {
    return { segments, prefix: "", replaceStart, quoted };
  }
  while (
    tokens[index]?.value === "." &&
    isIdentifier(tokens[index - 1])
  ) {
    segments.unshift(tokens[index - 1]!.value);
    replaceStart = tokens[index - 1]!.start;
    quoted ||= Boolean(tokens[index - 1]!.quoted);
    index -= 2;
  }
  return {
    segments,
    prefix: segments.at(-1) ?? "",
    replaceStart,
    quoted,
  };
}

function findFunctionCall(
  tokens: SqlToken[],
  cursor: number,
  metadata: CatalogueMetadata,
): SqlContext["functionCall"] {
  const stack: number[] = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index]!;
    if (token.start >= cursor) break;
    if (token.value === "(") stack.push(index);
    if (token.value === ")") stack.pop();
  }
  const open = stack.at(-1);
  if (open === undefined) return undefined;
  const name = tokens[open - 1];
  if (!isIdentifier(name)) return undefined;
  const definition = findFunction(metadata, name.value);
  const argumentIndex = tokens
    .slice(open + 1)
    .filter(
      (token) =>
        token.start < cursor &&
        token.depth === tokens[open]!.depth + 1 &&
        token.value === ",",
    ).length;
  return { definition, argumentIndex };
}

function cursorInsideLiteralOrComment(sql: string, cursor: number): boolean {
  const prefix = sql.slice(0, cursor);
  const tokens = tokenizeSql(prefix);
  const last = tokens.at(-1);
  if (last?.kind === "string" && last.end === cursor && !last.raw.endsWith("'")) {
    return true;
  }
  const line = prefix.slice(prefix.lastIndexOf("\n") + 1);
  if (line.includes("--")) return true;
  const blockStart = prefix.lastIndexOf("/*");
  return blockStart > prefix.lastIndexOf("*/");
}

function depthAt(sql: string, cursor: number): number {
  let depth = 0;
  for (const token of tokenizeSql(sql.slice(0, cursor))) {
    if (token.value === "(") depth += 1;
    if (token.value === ")") depth = Math.max(0, depth - 1);
  }
  return depth;
}

function isFromComma(tokens: SqlToken[], depth: number): boolean {
  const previous = tokens.at(-1);
  if (previous?.value !== "," || previous.depth !== depth) return false;
  const clause = [...tokens].reverse().find(
    (token) =>
      token.depth === depth &&
      token.kind === "keyword" &&
      clauseKeywords.has(token.value),
  );
  return clause?.value === "FROM";
}

function findRelation(
  metadata: CatalogueMetadata,
  segments: string[],
): CatalogueMetadataRelation | undefined {
  const name = segments.at(-1)!.toLocaleLowerCase();
  const schema = segments.length > 1
    ? segments.at(-2)!.toLocaleLowerCase()
    : undefined;
  return metadata.relations.find(
    (relation) =>
      relation.name.toLocaleLowerCase() === name &&
      (!schema || relation.schema_name.toLocaleLowerCase() === schema),
  );
}

function findFunction(
  metadata: CatalogueMetadata,
  name: string,
): CatalogueMetadataFunction | undefined {
  return metadata.functions.find(
    (definition) =>
      definition.name.toLocaleLowerCase() === name.toLocaleLowerCase(),
  );
}

function matchingClose(tokens: SqlToken[], open: number): number {
  const depth = tokens[open]!.depth;
  return tokens.findIndex(
    (token, index) =>
      index > open && token.value === ")" && token.depth === depth,
  );
}

function splitAtCommas(tokens: SqlToken[], depth: number): SqlToken[][] {
  const groups: SqlToken[][] = [[]];
  for (const token of tokens) {
    if (token.value === "," && token.depth === depth) groups.push([]);
    else groups.at(-1)!.push(token);
  }
  return groups;
}

function unknownColumn(name: string): CatalogueMetadataColumn {
  return { name, data_type: "UNKNOWN", nullable: true };
}

function isIdentifier(
  token: SqlToken | undefined,
): token is SqlToken {
  return token?.kind === "identifier";
}

function scanQuoted(sql: string, start: number, quote: "'" | '"'): number {
  let index = start + 1;
  while (index < sql.length) {
    if (sql[index] !== quote) {
      index += 1;
      continue;
    }
    if (sql[index + 1] === quote) {
      index += 2;
      continue;
    }
    return index + 1;
  }
  return sql.length;
}

function unquoteIdentifier(raw: string): string {
  const value = raw.startsWith('"') ? raw.slice(1, raw.endsWith('"') ? -1 : undefined) : raw;
  return value.replaceAll('""', '"');
}

function isKeyword(value: string): boolean {
  return new Set([
    ...clauseKeywords,
    ...aliasStopKeywords,
    "WITH",
    "RECURSIVE",
    "AS",
    "BY",
    "AND",
    "OR",
    "NOT",
    "NULL",
    "ASC",
    "DESC",
  ]).has(value);
}
