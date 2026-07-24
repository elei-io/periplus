import { helpCommand } from "./help.js";
import { listGraphsCommand, showGraphCommand } from "./graphs.js";
import { clearCommand } from "./general.js";
import { CommandRegistry } from "../registry.js";

let registry: CommandRegistry;

registry = new CommandRegistry([
  helpCommand(() => registry),
  clearCommand,
  listGraphsCommand,
  showGraphCommand,
]);

export const commands = registry;
