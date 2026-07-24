export interface CrawlGraphSummary {
  id: string;
  slug: string;
  description: string | null;
  root_node_id: string | null;
  system_owned: boolean;
  created_at: string;
}

export interface CrawlGraphList {
  items: CrawlGraphSummary[];
  total: number;
}

export interface AtlasApi {
  graphs: {
    list(signal?: AbortSignal): Promise<CrawlGraphList>;
  };
}

export type CommandResult =
  | {
      kind: "message";
      text: string;
    }
  | {
      kind: "table";
      columns: string[];
      rows: unknown[][];
    }
  | {
      kind: "navigate";
      path: string;
      label: string;
    }
  | {
      kind: "clear";
    };

export interface CommandContext {
  api: AtlasApi;
  signal: AbortSignal;
}

export interface CommandInvocation {
  args: string[];
}

export interface CommandDefinition {
  path: readonly string[];
  summary: string;
  usage: string;
  examples: readonly string[];
  execute(
    invocation: CommandInvocation,
    context: CommandContext,
  ): Promise<CommandResult> | CommandResult;
}
