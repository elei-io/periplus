import { helpCommand } from "./help.js";
import {
  confirmGraphRunCommand,
  listGraphsCommand,
  runGraphCommand,
  showGraphCommand,
} from "./graphs.js";
import {
  cancelRunCommand,
  followRunCommand,
  listRunsCommand,
  openRunCommand,
  pauseRunCommand,
  resumeRunCommand,
  runFailuresCommand,
  showRunCommand,
} from "./runs.js";
import {
  clearCommand,
  describeCommand,
  historyCommand,
  reloadCompletionCommand,
  statusCommand,
} from "./general.js";
import { CommandRegistry } from "../registry.js";
import { askAiCommand } from "./ai.js";

let registry: CommandRegistry;

registry = new CommandRegistry([
  helpCommand(() => registry),
  clearCommand,
  historyCommand,
  statusCommand,
  describeCommand,
  reloadCompletionCommand,
  askAiCommand,
  listGraphsCommand,
  showGraphCommand,
  runGraphCommand,
  confirmGraphRunCommand,
  listRunsCommand,
  showRunCommand,
  followRunCommand,
  runFailuresCommand,
  pauseRunCommand,
  resumeRunCommand,
  cancelRunCommand,
  openRunCommand,
]);

export const commands = registry;
