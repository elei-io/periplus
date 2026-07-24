import { ConsoleError } from "./errors.js";
import type { CommandDefinition } from "./types.js";

export class CommandRegistry {
  private readonly definitions: readonly CommandDefinition[];

  constructor(definitions: readonly CommandDefinition[]) {
    const paths = new Set<string>();
    for (const definition of definitions) {
      if (definition.path.length === 0) {
        throw new Error("A command path cannot be empty.");
      }
      const key = definition.path.join(" ");
      if (paths.has(key)) throw new Error(`Duplicate command: .${key}`);
      if (!definition.examples.length) {
        throw new Error(`Command .${key} must declare an example.`);
      }
      paths.add(key);
    }
    this.definitions = Object.freeze([...definitions]);
  }

  all(): readonly CommandDefinition[] {
    return this.definitions;
  }

  resolve(tokens: readonly string[]): {
    definition: CommandDefinition;
    args: string[];
  } {
    const candidates = this.definitions
      .filter((definition) =>
        definition.path.every(
          (part, index) => tokens[index]?.toLocaleLowerCase() === part,
        ),
      )
      .sort((left, right) => right.path.length - left.path.length);
    const definition = candidates[0];
    if (!definition) {
      const attempted = tokens.length ? `.${tokens.join(" ")}` : "empty input";
      throw new ConsoleError(`Unknown command: ${attempted}. Try .help.`);
    }
    return {
      definition,
      args: tokens.slice(definition.path.length),
    };
  }
}
