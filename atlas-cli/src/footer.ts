import type { ConsoleStatus } from "@atlas/console-core";

const DIM = "\u001b[2m";
const GREEN = "\u001b[32m";
const RED = "\u001b[31m";
const YELLOW = "\u001b[33m";
const CYAN = "\u001b[36m";
const RESET = "\u001b[0m";

export function renderFooter(
  status: ConsoleStatus,
  columns: number,
): string {
  const connection =
    status.connection.state === "connected"
      ? `${GREEN}● connected${RESET}`
      : status.connection.state === "connecting"
        ? `${DIM}◌ connecting${RESET}`
        : `${RED}○ disconnected${RESET}`;
  const schema = `${DIM}schema ${safeText(status.schemaVersion ?? "—")}${RESET}`;
  const lake = `${DIM}lake ${safeText(status.lakeSlug ?? "—")}${RESET}`;
  const compiler = renderCompiler(status);
  const colored = [connection, lake, schema, compiler].filter(Boolean).join("   ");
  const plain = stripAnsi(colored);
  const available = Math.max(1, columns - 1);
  return plain.length <= available
    ? colored
    : `${DIM}${truncate(plain, available)}${RESET}`;
}

function renderCompiler(status: ConsoleStatus): string {
  switch (status.compiler.state) {
    case "idle":
    case "debouncing":
      return "";
    case "checking":
      return `${DIM}◌ checking${RESET}`;
    case "valid":
      return `${GREEN}✓ valid${RESET}`;
    case "optimized": {
      const count = status.compiler.rewriteCount;
      const detail = count > 0
        ? ` · ${count} ${count === 1 ? "rewrite" : "rewrites"}`
        : "";
      return `${CYAN}⚡ optimized${detail}${RESET}`;
    }
    case "diagnostics": {
      const errors = status.compiler.diagnostics.filter(
        (item) => item.severity === "error",
      );
      const diagnostics = errors.length > 0
        ? errors
        : status.compiler.diagnostics;
      if (diagnostics.length === 0) return `${RED}✗ invalid${RESET}`;
      const isError = errors.length > 0;
      const color = isError ? RED : YELLOW;
      const symbol = isError ? "✗" : "⚠";
      if (diagnostics.length === 1) {
        return `${color}${symbol} ${safeText(diagnostics[0]!.message)}${RESET}`;
      }
      return `${color}${symbol} ${diagnostics.length} ${isError ? "errors" : "warnings"}${RESET}`;
    }
    case "unavailable":
      return `${DIM}compiler unavailable${RESET}`;
  }
}

function safeText(value: string): string {
  return value
    .replace(/\r\n|\r|\n/g, " ")
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, "");
}

function stripAnsi(value: string): string {
  return value.replace(/\u001b\[[0-9;]*m/g, "");
}

function truncate(value: string, available: number): string {
  if ([...value].length <= available) return value;
  if (available === 1) return "…";
  return `${[...value].slice(0, available - 1).join("")}…`;
}
