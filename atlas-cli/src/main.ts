#!/usr/bin/env node

import { createInterface } from "node:readline/promises";
import {
  AtlasConsole,
  ConsoleError,
  headlessCommandLine,
  HttpAtlasApi,
} from "@atlas/console-core";
import { parseArguments } from "./arguments.js";
import { openBrowser, resourceUrl } from "./browser.js";
import { resolveApiUrl, resolveWebUrl } from "./config.js";
import { renderResult } from "./output.js";

async function main(): Promise<void> {
  const options = parseArguments(process.argv.slice(2));
  const apiUrl = await resolveApiUrl(options.apiUrl);
  const webUrl = await resolveWebUrl(options.webUrl);
  const console = new AtlasConsole(new HttpAtlasApi(apiUrl));

  if (options.command.length) {
    const result = await console.execute(headlessCommandLine(options.command));
    if (result) {
      if (result.kind === "navigate") {
        await openBrowser(resourceUrl(webUrl, result.path));
      }
      process.stdout.write(
        renderResult(result, {
          format: options.format,
          columns: process.stdout.columns,
        }),
      );
    }
    return;
  }

  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new ConsoleError(
      "Interactive mode requires a terminal. Provide a command to run headlessly.",
    );
  }

  const terminal = createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: true,
  });
  process.stdout.write("Atlas console. Enter .help for commands.\n");
  try {
    while (true) {
      const line = await terminal.question("atlas> ").catch(() => ".exit");
      if (line.trim() === ".exit") break;
      try {
        const result = await console.execute(line);
        if (result) {
          if (result.kind === "navigate") {
            await openBrowser(resourceUrl(webUrl, result.path));
          }
          process.stdout.write(
            renderResult(result, {
              format: "table",
              columns: process.stdout.columns,
            }),
          );
        }
      } catch (reason) {
        process.stderr.write(`Error: ${errorMessage(reason)}\n`);
      }
    }
  } finally {
    terminal.close();
  }
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

main().catch((reason: unknown) => {
  process.stderr.write(`atlas: ${errorMessage(reason)}\n`);
  process.exitCode = 1;
});
