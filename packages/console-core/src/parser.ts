import { ConsoleError } from "./errors.js";

export function parseCommandLine(line: string): string[] {
  const input = line.trim();
  if (!input) return [];
  if (!input.startsWith(".")) {
    throw new ConsoleError("Atlas commands start with a dot. Try .help.");
  }

  const tokens: string[] = [];
  let token = "";
  let quote: "'" | '"' | undefined;
  let escaping = false;

  for (const character of input.slice(1)) {
    if (escaping) {
      token += character;
      escaping = false;
      continue;
    }
    if (character === "\\") {
      escaping = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = undefined;
      else token += character;
      continue;
    }
    if (character === "'" || character === '"') {
      quote = character;
      continue;
    }
    if (/\s/.test(character)) {
      if (token) {
        tokens.push(token);
        token = "";
      }
      continue;
    }
    token += character;
  }

  if (escaping) token += "\\";
  if (quote) throw new ConsoleError("Unterminated quoted value.");
  if (token) tokens.push(token);
  return tokens;
}

export function headlessCommandLine(args: string[]): string {
  if (args.length === 0) return "";
  if (args[0]?.startsWith(".")) return args.join(" ");
  return `.${args.join(" ")}`;
}
