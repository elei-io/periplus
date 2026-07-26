import type { AtomicCommandResult } from "@atlas/console-core";

export function renderResult(
  result: AtomicCommandResult,
  options: {
    format: "table" | "json";
    columns?: number;
  },
): string {
  if (options.format === "json") {
    return `${JSON.stringify(result, jsonReplacer, 2)}\n`;
  }
  if (result.kind === "progress") {
    const symbol =
      result.state === "completed" ? "\u001b[32m✓\u001b[0m" : "\u001b[31m✗\u001b[0m";
    const duration = result.durationMilliseconds === undefined
      ? ""
      : ` \u001b[2m· ${formatDuration(result.durationMilliseconds)}\u001b[0m`;
    return `\u001b[2m│\u001b[0m ${symbol} ${result.label}${duration}\n`;
  }
  if (result.kind === "assistant") {
    return renderAssistant(result, options.columns ?? 100);
  }
  if (result.kind === "message") return `${result.text}\n`;
  if (result.kind === "navigate") return `${result.label}\n`;
  if (result.kind === "copy") return `${result.label}\n`;
  if (result.kind === "clear") return "\u001b[2J\u001b[H";
  const table = formatTable(
    result.columns,
    result.rows,
    options.columns ?? 100,
  ).join("\n");
  return `${table}${result.summary ? `\n${result.summary}` : ""}\n`;
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
  if (value === null || value === undefined) return "NULL";
  if (value instanceof Uint8Array) {
    return `0x${[...value].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "object") return JSON.stringify(value, jsonReplacer);
  return String(value).replaceAll(/\s+/g, " ");
}

function jsonReplacer(_key: string, value: unknown): unknown {
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Uint8Array) {
    return `0x${[...value].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  return value;
}

export function startProgress(
  message: string,
  delayMilliseconds = 150,
  prefix = "",
): { stop(): void } {
  if (!process.stdout.isTTY) return { stop() {} };
  const frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
  const startedAt = performance.now();
  let frame = 0;
  let visible = false;
  const render = () => {
    visible = true;
    const elapsed = ((performance.now() - startedAt) / 1_000).toFixed(1);
    process.stdout.write(
      `\r\u001b[2K${prefix}\u001b[1;34m${frames[frame % frames.length]}\u001b[0m ${message} \u001b[2m${elapsed}s\u001b[0m`,
    );
    frame += 1;
  };
  let interval: ReturnType<typeof setInterval> | undefined;
  const delay = setTimeout(() => {
    render();
    interval = setInterval(render, 80);
  }, delayMilliseconds);
  return {
    stop() {
      clearTimeout(delay);
      if (interval) clearInterval(interval);
      if (visible) process.stdout.write("\r\u001b[2K");
    },
  };
}

function formatDuration(milliseconds: number): string {
  return milliseconds < 1_000
    ? `${milliseconds}ms`
    : `${(milliseconds / 1_000).toFixed(1)}s`;
}

function renderAssistant(
  result: Extract<AtomicCommandResult, { kind: "assistant" }>,
  columns: number,
): string {
  const width = Math.max(24, Math.min(100, columns - 4));
  const lines = wrapText(result.text, width);
  const error = result.state === "failed";
  if (!result.sql) {
    const answer = lines.map((line, index) => {
      const prefix = index === 0 ? "└ " : "  ";
      return `\u001b[2m${prefix}\u001b[0m${
        error ? `\u001b[31m${line}\u001b[0m` : line
      }`;
    }).join("\n");
    const suggestions = (result.suggestions ?? [])
      .map(
        (suggestion) =>
          `\u001b[2m│\u001b[0m  \u001b[34m${suggestion.index}.\u001b[0m ${suggestion.title}` +
          ` \u001b[2m— ${suggestion.description}\u001b[0m`,
      )
      .join("\n");
    const actions = result.suggestions?.length
      ? "\u001b[2m└ .ai <number> · --copy · --show\u001b[0m\n\n"
      : "";
    return (
      `\u001b[2m│\u001b[0m\n${answer}\n` +
      (suggestions ? `\u001b[2m│\u001b[0m\n${suggestions}\n${actions}` : "\n")
    );
  }
  const answer = lines
    .map((line) => `\u001b[2m│\u001b[0m ${line}`)
    .join("\n");
  const sql = result.sql
    .replaceAll("\r\n", "\n")
    .split("\n")
    .map((line) => `\u001b[2m│   ${line}\u001b[0m`)
    .join("\n");
  return (
    `\u001b[2m│\u001b[0m\n${answer}\n\u001b[2m│\u001b[0m\n` +
    `${sql}\n\u001b[2m└ Run with ${result.sqlRunCommand ?? ".ai <number>"}\u001b[0m\n\n`
  );
}

function wrapText(value: string, width: number): string[] {
  const output: string[] = [];
  for (const paragraph of value.replaceAll("\r\n", "\n").split("\n")) {
    const words = paragraph.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) {
      output.push("");
      continue;
    }
    let line = "";
    for (const word of words) {
      if (line && line.length + word.length + 1 > width) {
        output.push(line);
        line = word;
      } else {
        line = line ? `${line} ${word}` : word;
      }
    }
    if (line) output.push(line);
  }
  return output.length ? output : [""];
}
