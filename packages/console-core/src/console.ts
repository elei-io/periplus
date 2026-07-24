import { commands } from "./commands/index.js";
import { parseCommandLine } from "./parser.js";
import type { AtlasApi, CommandResult } from "./types.js";

export class AtlasConsole {
  private activeCommand?: AbortController;

  constructor(private readonly api: AtlasApi) {}

  async execute(line: string): Promise<CommandResult | undefined> {
    const tokens = parseCommandLine(line);
    if (!tokens.length) return undefined;
    const { definition, args } = commands.resolve(tokens);
    const controller = new AbortController();
    this.activeCommand = controller;
    try {
      return await definition.execute(
        { args },
        { api: this.api, signal: controller.signal },
      );
    } finally {
      if (this.activeCommand === controller) this.activeCommand = undefined;
    }
  }

  interrupt(): boolean {
    if (!this.activeCommand || this.activeCommand.signal.aborted) return false;
    this.activeCommand.abort();
    return true;
  }
}
