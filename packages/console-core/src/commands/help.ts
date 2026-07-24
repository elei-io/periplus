import { ConsoleError } from "../errors.js";
import type { CommandDefinition } from "../types.js";
import type { CommandRegistry } from "../registry.js";

export function helpCommand(
  registry: () => CommandRegistry,
): CommandDefinition {
  return {
    path: ["help"],
    summary: "Show available Atlas commands",
    usage: ".help [resource]",
    examples: [".help", ".help graphs"],
    execute({ args }) {
      if (args.length > 1) throw new ConsoleError("Usage: .help [resource]");
      const resource = args[0]?.toLocaleLowerCase();
      const definitions = registry()
        .all()
        .filter(
          (definition) =>
            !resource || definition.path[0]?.toLocaleLowerCase() === resource,
        );
      if (resource && definitions.length === 0) {
        throw new ConsoleError(`Unknown command resource: ${resource}`);
      }
      return {
        kind: "table",
        columns: ["Command", "Description"],
        rows: definitions.map((definition) => [
          definition.usage,
          definition.summary,
        ]),
      };
    },
  };
}
