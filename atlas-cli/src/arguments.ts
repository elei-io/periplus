import { ConsoleError } from "@atlas/console-core";

export interface CliOptions {
  apiUrl?: string;
  webUrl?: string;
  format: "table" | "json";
  command: string[];
}

export function parseArguments(args: string[]): CliOptions {
  const command: string[] = [];
  let apiUrl: string | undefined;
  let webUrl: string | undefined;
  let format: CliOptions["format"] = "table";

  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index]!;
    if (argument === "--api-url") {
      apiUrl = requiredValue(args, ++index, "--api-url");
    } else if (argument === "--web-url") {
      webUrl = requiredValue(args, ++index, "--web-url");
    } else if (argument === "--format") {
      const value = requiredValue(args, ++index, "--format");
      if (value !== "table" && value !== "json") {
        throw new ConsoleError("--format must be table or json.");
      }
      format = value;
    } else {
      command.push(argument);
    }
  }

  return { apiUrl, webUrl, format, command };
}

function requiredValue(
  args: string[],
  index: number,
  option: string,
): string {
  const value = args[index];
  if (!value) throw new ConsoleError(`${option} requires a value.`);
  return value;
}
