import type { CommandResult } from "@atlas/console-core";

export function renderResult(
  result: CommandResult,
  options: {
    format: "table" | "json";
    columns?: number;
  },
): string {
  if (options.format === "json") return `${JSON.stringify(result, null, 2)}\n`;
  if (result.kind === "message") return `${result.text}\n`;
  if (result.kind === "navigate") return `${result.label}\n`;
  if (result.kind === "clear") return "\u001b[2J\u001b[H";
  return `${formatTable(
    result.columns,
    result.rows,
    options.columns ?? 100,
  ).join("\n")}\n`;
}

export function formatTable(
  columns: string[],
  rows: unknown[][],
  maximumWidth: number,
): string[] {
  const textRows = rows.map((row) =>
    columns.map((_column, index) => formatValue(row[index])),
  );
  const widths = columns.map((column, index) =>
    Math.max(
      column.length,
      ...textRows.map((row) => row[index]?.length ?? 0),
    ),
  );
  shrinkWidths(widths, Math.max(maximumWidth, columns.length * 4));
  const render = (row: string[]) =>
    row
      .map((value, index) => truncate(value, widths[index]!))
      .map((value, index) => value.padEnd(widths[index]!))
      .join("  ")
      .trimEnd();
  return [
    render(columns),
    render(widths.map((width) => "─".repeat(width))),
    ...textRows.map(render),
  ];
}

function shrinkWidths(widths: number[], maximumWidth: number): void {
  const separators = Math.max(0, widths.length - 1) * 2;
  while (
    widths.reduce((total, width) => total + width, separators) > maximumWidth
  ) {
    const widest = Math.max(...widths);
    const index = widths.indexOf(widest);
    if (widest <= 3 || index < 0) break;
    widths[index] = widest - 1;
  }
}

function truncate(value: string, width: number): string {
  if (value.length <= width) return value;
  return width <= 1 ? "…" : `${value.slice(0, width - 1)}…`;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value).replaceAll(/\s+/g, " ");
}
