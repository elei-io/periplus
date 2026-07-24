import { ConsoleError } from "../errors.js";
import type { CommandDefinition } from "../types.js";

export const clearCommand: CommandDefinition = {
  path: ["clear"],
  summary: "Clear the console",
  usage: ".clear",
  examples: [".clear"],
  execute({ args }) {
    if (args.length) throw new ConsoleError("Usage: .clear");
    return { kind: "clear" };
  },
};
