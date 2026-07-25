import type {
  CommandArgumentDefinition,
  CommandDefinition,
  CommandOptionDefinition,
} from "./types.js";

export type CommandDeclaration = Omit<CommandDefinition, "usage"> & {
  usage?: string;
};

export function defineCommand(
  declaration: CommandDeclaration,
): CommandDefinition {
  return Object.freeze({
    ...declaration,
    usage: declaration.usage ?? commandUsage(declaration),
  });
}

export const argument = {
  string(
    name: string,
    details: Omit<CommandArgumentDefinition, "name"> = {},
  ): CommandArgumentDefinition {
    return { name, required: true, ...details };
  },

  choice(
    name: string,
    choices: readonly string[],
    details: Omit<
      CommandArgumentDefinition,
      "name" | "choices"
    > = {},
  ): CommandArgumentDefinition {
    return { name, required: true, choices, ...details };
  },
};

export const option = {
  boolean(
    name: string,
    details: Omit<CommandOptionDefinition, "name" | "type"> = {},
  ): CommandOptionDefinition {
    return { name, type: "boolean", ...details };
  },

  string(
    name: string,
    details: Omit<CommandOptionDefinition, "name" | "type"> = {},
  ): CommandOptionDefinition {
    return { name, type: "string", ...details };
  },

  strings(
    name: string,
    details: Omit<
      CommandOptionDefinition,
      "name" | "type" | "repeatable"
    > = {},
  ): CommandOptionDefinition {
    return { name, type: "string", repeatable: true, ...details };
  },

  integer(
    name: string,
    details: Omit<CommandOptionDefinition, "name" | "type"> = {},
  ): CommandOptionDefinition {
    return { name, type: "integer", ...details };
  },

  choice(
    name: string,
    choices: readonly string[],
    details: Omit<
      CommandOptionDefinition,
      "name" | "type" | "choices"
    > = {},
  ): CommandOptionDefinition {
    return { name, type: "string", choices, ...details };
  },
};

function commandUsage(declaration: CommandDeclaration): string {
  const path = `.${declaration.path.join(" ")}`;
  const argumentsUsage = (declaration.arguments ?? [])
    .map((item) =>
      item.required === false ? `[${item.name}]` : `<${item.name}>`,
    )
    .join(" ");
  const optionsUsage = (declaration.options ?? [])
    .map((item) =>
      item.type === "boolean"
        ? `[--${item.name}]`
        : `[--${item.name} <value>${item.repeatable ? " ...": ""}]`,
    )
    .join(" ");
  return [path, argumentsUsage, optionsUsage].filter(Boolean).join(" ");
}
