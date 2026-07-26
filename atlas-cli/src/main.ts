#!/usr/bin/env node

import {
  AtlasConsole,
  type AtomicCommandResult,
  type CommandResult,
  ConsoleError,
  GhostTextEditor,
  headlessCommandLine,
  HttpAtlasApi,
} from "@atlas/console-core";
import { parseArguments } from "./arguments.js";
import { openBrowser, resourceUrl } from "./browser.js";
import { resolveApiUrl, resolveWebUrl } from "./config.js";
import { renderResult, startProgress } from "./output.js";
import { renderFooter } from "./footer.js";
import { NodeTerminal } from "./terminal.js";
import { renderWelcome } from "./welcome.js";

async function main(): Promise<void> {
  const options = parseArguments(process.argv.slice(2));
  const apiUrl = await resolveApiUrl(options.apiUrl);
  const webUrl = await resolveWebUrl(options.webUrl);
  const console = new AtlasConsole(new HttpAtlasApi(apiUrl));

  if (options.command.length) {
    const result = await console.execute(headlessCommandLine(options.command));
    if (result) {
      await emitResult(result, webUrl, options.format);
    }
    return;
  }

  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new ConsoleError(
      "Interactive mode requires a terminal. Provide a command to run headlessly.",
    );
  }

  const terminal = new NodeTerminal();
  await console.status.connect(AbortSignal.timeout(3_000));
  terminal.clearScreen();
  process.stdout.write(renderWelcome(console.status.snapshot()));
  const editor = new GhostTextEditor(
    terminal,
    (input, cursor) => console.complete(input, cursor),
    75,
    console.history,
    (input) => console.status.updateInput(input),
  );
  terminal.enablePinnedFooter(
    (columns) => renderFooter(console.status.snapshot(), columns),
  );
  const statusSubscription = console.status.subscribe(() => {
    terminal.refreshFooter();
  });
  const interrupt = terminal.onData((data) => {
    if (data === "\u0003" && console.interrupt()) {
      terminal.writeRaw("^C\r\n");
    }
  });
  try {
    while (true) {
      const line = await editor.readLine("atlas> ");
      if (line.trim() === ".exit") break;
      const progress = line.trim() && !line.trimStart().startsWith(".")
        ? startProgress("Running query…")
        : undefined;
      let progressStopped = false;
      const stopProgress = () => {
        if (progressStopped) return;
        progress?.stop();
        progressStopped = true;
      };
      try {
        const result = await console.execute(line);
        stopProgress();
        if (result) {
          await emitResult(result, webUrl, "table");
        }
      } catch (reason) {
        stopProgress();
        if (isAbort(reason)) {
          process.stderr.write("Query cancellation requested.\n");
        } else {
          process.stderr.write(`Error: ${errorMessage(reason)}\n`);
        }
      } finally {
        stopProgress();
      }
    }
  } finally {
    interrupt.dispose();
    statusSubscription.dispose();
    console.status.dispose();
    terminal.close();
  }
}

async function emitResult(
  result: CommandResult,
  webUrl: string,
  format: "table" | "json",
): Promise<void> {
  let activity: { stop(): void } | undefined;
  let transientLines = 0;
  const emit = async (event: AtomicCommandResult) => {
    if (
      format === "table" &&
      event.kind === "progress" &&
      event.state === "active"
    ) {
      activity?.stop();
      if (event.groupStart) {
        process.stdout.write("\n\u001b[1;35m◆ Atlas\u001b[0m\n");
      }
      activity = startProgress(event.label, 0, "\u001b[2m│\u001b[0m ");
      return;
    }
    activity?.stop();
    activity = undefined;
    if (format === "table" && process.stdout.isTTY && transientLines > 0) {
      process.stdout.write("\u001b[1A\u001b[2K".repeat(transientLines));
      transientLines = 0;
    }
    const rendered = await emitAtomicResult(event, webUrl, format);
    if (
      format === "table" &&
      process.stdout.isTTY &&
      event.kind === "table" &&
      event.transient
    ) {
      transientLines = rendered.split("\n").length - 1;
    }
  };
  if (result.kind === "stream") {
    try {
      for await (const event of result.events) {
        await emit(event);
      }
    } finally {
      activity?.stop();
    }
    return;
  }
  await emit(result);
}

async function emitAtomicResult(
  result: AtomicCommandResult,
  webUrl: string,
  format: "table" | "json",
): Promise<string> {
  if (result.kind === "navigate") {
    await openBrowser(resourceUrl(webUrl, result.path));
  }
  if (result.kind === "copy") {
    process.stdout.write(`\u001b]52;c;${Buffer.from(result.text).toString("base64")}\u0007`);
  }
  const rendered = renderResult(result, {
    format,
    columns: process.stdout.columns,
  });
  process.stdout.write(rendered);
  return rendered;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function isAbort(reason: unknown): boolean {
  return reason instanceof Error && reason.name === "AbortError";
}

main().catch((reason: unknown) => {
  process.stderr.write(`atlas: ${errorMessage(reason)}\n`);
  process.exitCode = 1;
});
