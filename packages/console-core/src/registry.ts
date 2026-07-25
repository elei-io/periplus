import { ConsoleError } from "./errors.js";
import { choiceCompletion, safeCompletions } from "./completion.js";
import type {
  CommandContext,
  CommandDefinition,
  CommandInvocation,
  CompletionItem,
} from "./types.js";

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

  invocation(
    definition: CommandDefinition,
    args: readonly string[],
  ): CommandInvocation {
    const positionalValues: string[] = [];
    const options: Record<
      string,
      string | number | boolean | readonly string[]
    > = {};
    for (let index = 0; index < args.length; index += 1) {
      const token = args[index]!;
      if (!token.startsWith("--")) {
        positionalValues.push(token);
        continue;
      }
      const name = token.slice(2);
      const option = definition.options?.find((item) => item.name === name);
      if (!option) throw new ConsoleError(`Unknown option: --${name}`);
      if (name in options && !option.repeatable) {
        throw new ConsoleError(`Option may only be used once: --${name}`);
      }
      if (option.type === "boolean") {
        options[name] = true;
        continue;
      }
      const value = args[++index];
      if (!value || value.startsWith("--")) {
        throw new ConsoleError(`Option --${name} requires a value.`);
      }
      if (option.choices && !option.choices.includes(value)) {
        throw new ConsoleError(
          `Option --${name} must be one of: ${option.choices.join(", ")}.`,
        );
      }
      if (option.type === "integer") {
        if (!/^-?\d+$/.test(value)) {
          throw new ConsoleError(`Option --${name} requires an integer.`);
        }
        const parsed = Number(value);
        if (!Number.isSafeInteger(parsed)) {
          throw new ConsoleError(`Option --${name} requires a safe integer.`);
        }
        if (option.minimum !== undefined && parsed < option.minimum) {
          throw new ConsoleError(
            `Option --${name} must be at least ${option.minimum}.`,
          );
        }
        if (option.maximum !== undefined && parsed > option.maximum) {
          throw new ConsoleError(
            `Option --${name} must be at most ${option.maximum}.`,
          );
        }
        options[name] = parsed;
      } else if (option.repeatable) {
        const existing = options[name];
        options[name] = [
          ...(Array.isArray(existing) ? existing : []),
          value,
        ];
      } else {
        options[name] = value;
      }
    }

    const declarations = definition.arguments ?? [];
    const required = declarations.filter((item) => item.required !== false);
    if (
      positionalValues.length < required.length ||
      positionalValues.length > declarations.length
    ) {
      throw new ConsoleError(`Usage: ${definition.usage}`);
    }
    const positionals: Record<string, string | undefined> = {};
    declarations.forEach((item, index) => {
      const value = positionalValues[index];
      if (value && item.choices && !item.choices.includes(value)) {
        throw new ConsoleError(
          `${item.name} must be one of: ${item.choices.join(", ")}.`,
        );
      }
      positionals[item.name] = value;
    });
    return {
      positionals: Object.freeze(positionals),
      options: Object.freeze(options),
    };
  }

  async complete(
    input: string,
    cursor: number,
    context: CommandContext,
  ): Promise<CompletionItem[]> {
    const beforeCursor = input.slice(0, cursor);
    const tokens = positionedTokens(beforeCursor.slice(1), 1);
    const trailingSpace = /\s$/.test(beforeCursor);
    const current = trailingSpace
      ? { value: "", start: cursor, end: cursor }
      : tokens.at(-1) ?? { value: "", start: cursor, end: cursor };
    const completed = trailingSpace ? tokens : tokens.slice(0, -1);

    if (completed.length === 0) {
      const resources = new Map<string, CommandDefinition>();
      for (const definition of this.definitions) {
        resources.set(definition.path[0]!, definition);
      }
      return safeCompletions(
        [...resources].filter(([resource]) =>
          resource.startsWith(current.value.toLocaleLowerCase()),
        ).map(([resource, definition]) => ({
          insertText: `.${resource}`,
          replaceStart: 0,
          replaceEnd: cursor,
          kind: "resource",
          description: definition.summary,
        })),
      );
    }

    const resource = completed[0]?.value.toLocaleLowerCase();
    if (completed.length === 1) {
      const actions = new Map<string, CommandDefinition>();
      for (const definition of this.definitions) {
        if (definition.path[0] === resource && definition.path[1]) {
          actions.set(definition.path[1], definition);
        }
      }
      if (actions.size > 0) {
        return safeCompletions(
          [...actions].filter(([action]) =>
            action.startsWith(current.value.toLocaleLowerCase()),
          ).map(([action, definition]) => ({
            insertText: action,
            replaceStart: current.start,
            replaceEnd: cursor,
            kind: "action",
            description: definition.summary,
          })),
        );
      }
    }

    const pathTokens = [...completed.map((token) => token.value), current.value];
    const definition = this.definitions.find((candidate) =>
      candidate.path.every(
        (part, index) =>
          pathTokens[index]?.toLocaleLowerCase() === part,
      ),
    );
    if (!definition) return [];

    if (current.value.startsWith("--")) {
      const used = new Set(
        tokens
          .map((token) => token.value)
          .filter((value) => value.startsWith("--"))
          .map((value) => value.slice(2)),
      );
      return safeCompletions(
        (definition.options ?? [])
          .filter((item) => item.repeatable || !used.has(item.name))
          .filter((item) =>
            item.name.startsWith(current.value.slice(2).toLocaleLowerCase()),
          )
          .map((item) => ({
            insertText: `--${item.name}`,
            replaceStart: current.start,
            replaceEnd: cursor,
            kind: "option",
            description: item.description,
          })),
      );
    }

    const afterPath = tokens.slice(definition.path.length);
    const previous = trailingSpace ? tokens.at(-1) : tokens.at(-2);
    if (previous?.value.startsWith("--")) {
      const selected = definition.options?.find(
        (item) => item.name === previous.value.slice(2),
      );
      const provider = selected?.complete ??
        (selected?.choices
          ? choiceCompletion(selected.choices)
          : undefined);
      if (provider) {
        return safeCompletions(
          await provider({
            input,
            cursor,
            prefix: current.value,
            replaceStart: current.start,
            context,
          }),
        );
      }
    }
    const positional = afterPath.filter(
      (token) => !token.value.startsWith("--"),
    );
    const argumentIndex = Math.max(
      0,
      positional.length - (trailingSpace ? 0 : 1),
    );
    const argument = definition.arguments?.[argumentIndex];
    if (!argument) return [];
    const provider = argument.complete ??
      (argument.choices
        ? choiceCompletion(argument.choices)
        : undefined);
    if (!provider) return [];
    return safeCompletions(
      await provider({
        input,
        cursor,
        prefix: current.value,
        replaceStart: current.start,
        context,
      }),
    );
  }
}

interface PositionedToken {
  value: string;
  start: number;
  end: number;
}

function positionedTokens(input: string, offset: number): PositionedToken[] {
  const tokens: PositionedToken[] = [];
  for (const match of input.matchAll(/\S+/g)) {
    tokens.push({
      value: match[0],
      start: offset + match.index,
      end: offset + match.index + match[0].length,
    });
  }
  return tokens;
}
