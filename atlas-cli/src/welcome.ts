import type { ConsoleStatus } from "@atlas/console-core";

const WORDMARK = [
  "     ___  ________  ___   _____",
  "    / _ |/_  __/ / / _ | / ___/",
  "   / __ | / / / /_/ __ |(__  )",
  "  /_/ |_|/_/ /___/_/ |_/____/",
].join("\n");

export function renderWelcome(status: ConsoleStatus): string {
  const lake = status.lakeSlug ?? "—";
  const schema = status.schemaVersion ?? "—";
  const compiler = status.compilerVersion ?? "—";
  return [
    "",
    WORDMARK,
    "",
    "The web is messy. Let's make it queryable.",
    `Lake ${lake} · Atlas schema ${schema} · compiler ${compiler} · .help knows the terrain.`,
    "",
    "",
  ].join("\n");
}
