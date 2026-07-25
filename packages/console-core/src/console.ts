import { commands } from "./commands/index.js";
import { safeCompletions } from "./completion.js";
import { parseCommandLine } from "./parser.js";
import { SqlCompleter } from "./sql-completion.js";
import { LiveConsoleStatus } from "./live-status.js";
import type {
  AtlasApi,
  AtomicCommandResult,
  AiSqlSuggestion,
  CommandResult,
  CompletionItem,
} from "./types.js";

export class AtlasConsole {
  private activeCommand?: AbortController;
  private activeCompletion?: AbortController;
  private readonly sqlCompleter: SqlCompleter;
  readonly status: LiveConsoleStatus;
  readonly history: string[] = [];
  private readonly session: {
    history: readonly string[];
    lastRunId?: string;
    aiHistory: import("./types.js").AiMessage[];
    lastAiSuggestions?: AiSqlSuggestion[];
  };

  constructor(private readonly api: AtlasApi) {
    this.sqlCompleter = new SqlCompleter(api);
    this.status = new LiveConsoleStatus(api);
    this.session = { history: this.history, aiHistory: [] };
  }

  async complete(
    input: string,
    cursor = input.length,
  ): Promise<CompletionItem[]> {
    this.activeCompletion?.abort();
    const controller = new AbortController();
    this.activeCompletion = controller;
    try {
      const items = input.startsWith(".")
        ? await commands.complete(input, cursor, this.context(controller.signal))
        : await this.sqlCompleter.complete(input, cursor, controller.signal);
      return safeCompletions(items);
    } finally {
      if (this.activeCompletion === controller) {
        this.activeCompletion = undefined;
      }
    }
  }

  async execute(line: string): Promise<CommandResult | undefined> {
    const input = line.trim();
    if (!input) return undefined;
    const controller = new AbortController();
    this.activeCommand = controller;
    let streamOwnsController = false;
    try {
      if (!input.startsWith(".")) {
        const startedAt = performance.now();
        const result = await this.api.catalogue.execute(
          input,
          controller.signal,
        );
        return {
          kind: "table",
          columns: result.columns,
          rows: result.rows,
          summary: `${result.rows.length} ${result.rows.length === 1 ? "row" : "rows"} · ${formatDuration(performance.now() - startedAt)}`,
        };
      }
      const tokens = parseCommandLine(input);
      const { definition, args } = commands.resolve(tokens);
      const result = await definition.execute(
        commands.invocation(definition, args),
        this.context(controller.signal),
      );
      if (result.kind !== "stream") return result;
      streamOwnsController = true;
      return {
        kind: "stream",
        events: this.holdStream(result.events, controller),
      };
    } finally {
      if (!streamOwnsController && this.activeCommand === controller) {
        this.activeCommand = undefined;
      }
    }
  }

  interrupt(): boolean {
    if (!this.activeCommand || this.activeCommand.signal.aborted) return false;
    this.activeCommand.abort();
    return true;
  }

  private context(signal: AbortSignal) {
    return {
      api: this.api,
      signal,
      session: this.session,
      completion: {
        reload: (reloadSignal?: AbortSignal) =>
          this.sqlCompleter.reload(reloadSignal ?? signal),
      },
    };
  }

  private async *holdStream(
    events: AsyncIterable<AtomicCommandResult>,
    controller: AbortController,
  ): AsyncIterable<AtomicCommandResult> {
    try {
      yield* events;
    } finally {
      if (this.activeCommand === controller) this.activeCommand = undefined;
    }
  }
}

function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${Math.max(1, Math.round(milliseconds))}ms`;
  return `${(milliseconds / 1_000).toFixed(1)}s`;
}
