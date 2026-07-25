import { ConsoleError } from "../errors.js";
import { argument, defineCommand } from "../command.js";
import type { CommandDefinition } from "../types.js";
import type { CommandRegistry } from "../registry.js";

export function helpCommand(
  registry: () => CommandRegistry,
): CommandDefinition {
  return defineCommand({
    path: ["help"],
    summary: "Show available Atlas commands",
    arguments: [
      argument.string("resource", {
        required: false,
        description: "Command resource to inspect",
        complete: ({ context: _context, cursor, prefix, replaceStart }) =>
          [...new Set(registry().all().map((item) => item.path[0]!))]
            .filter((resource) => resource.startsWith(prefix))
            .map((resource) => ({
              insertText: resource,
              replaceStart,
              replaceEnd: cursor,
              kind: "resource",
            })),
      }),
    ],
    examples: [".help", ".help graphs"],
    execute({ positionals }) {
      const resource = positionals.resource?.toLocaleLowerCase();
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
  });
}
