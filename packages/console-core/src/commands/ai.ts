import { argument, defineCommand, option } from "../command.js";
import { ConsoleError } from "../errors.js";
import type {
  AiEvent,
  AiMessage,
  AiSqlSuggestion,
  AtomicCommandResult,
} from "../types.js";

const DEFAULT_CONTEXT_MESSAGES = 6;

export const askAiCommand = defineCommand({
  path: ["ai"],
  summary: "Ask Atlas AI about catalogue data",
  arguments: [
    argument.string("prompt", {
      description: "Question about the retained catalogue",
    }),
  ],
  options: [
    option.integer("context", {
      description: "Number of recent AI messages to include",
      minimum: 0,
      maximum: 20,
    }),
  ],
  examples: ['.ai "How are book prices distributed?"'],
  execute({ positionals, options }, context) {
    const prompt = positionals.prompt!;
    const contextLength =
      typeof options.context === "number"
        ? options.context
        : DEFAULT_CONTEXT_MESSAGES;
    const aiHistory = context.session.aiHistory ??= [];
    context.session.lastAiSuggestions = [];
    const recent =
      contextLength === 0
        ? []
        : aiHistory.slice(-contextLength);
    return {
      kind: "stream",
      events: aiResults(
        context.api.ai.ask(prompt, recent, context.signal),
        prompt,
        aiHistory,
        (suggestions) => {
          context.session.lastAiSuggestions = suggestions;
        },
      ),
    };
  },
});

export const runAiSqlCommand = defineCommand({
  path: ["ai", "run"],
  summary: "Run a SQL query suggested by Atlas AI",
  arguments: [
    argument.string("suggestion", {
      description: "One-based suggestion number",
      required: false,
    }),
  ],
  examples: [".ai run 2"],
  async execute({ positionals }, context) {
    const suggestion = selectSuggestion(
      context.session.lastAiSuggestions,
      positionals.suggestion,
    );
    const startedAt = performance.now();
    const result = await context.api.catalogue.execute(
      suggestion.authored_sql,
      context.signal,
    );
    const elapsed = Math.max(1, Math.round(performance.now() - startedAt));
    return {
      kind: "table",
      columns: result.columns,
      rows: result.rows,
      summary: `${result.rows.length} ${result.rows.length === 1 ? "row" : "rows"} · ${elapsed}ms`,
    };
  },
});

export const showAiSqlCommand = defineCommand({
  path: ["ai", "show"],
  summary: "Show a SQL query suggested by Atlas AI",
  arguments: [
    argument.string("suggestion", {
      description: "One-based suggestion number",
    }),
  ],
  examples: [".ai show 2"],
  execute({ positionals }, context) {
    const suggestion = selectSuggestion(
      context.session.lastAiSuggestions,
      positionals.suggestion,
    );
    return {
      kind: "assistant",
      state: "completed",
      text: `${suggestion.title}\n${suggestion.description}`,
      sql: suggestion.authored_sql,
      sqlRunCommand: `.ai run ${positionals.suggestion}`,
    };
  },
});

async function* aiResults(
  events: AsyncIterable<AiEvent>,
  prompt: string,
  history: AiMessage[],
  rememberSuggestions: (suggestions: AiSqlSuggestion[]) => void,
): AsyncIterable<AtomicCommandResult> {
  let completed = false;
  yield {
    kind: "progress",
    state: "active",
    activity: "thinking",
    label: "Thinking",
    groupStart: true,
  };
  for await (const event of events) {
    if (event.type === "tool.started") {
      yield {
        kind: "progress",
        state: "active",
        activity: toolActivity(event.tool),
        label: toolLabel(event.tool),
      };
    } else if (event.type === "tool.completed") {
      yield {
        kind: "progress",
        state: "completed",
        activity: toolActivity(event.tool),
        label: toolLabel(event.tool),
        durationMilliseconds: event.duration_ms ?? undefined,
      };
      yield {
        kind: "progress",
        state: "active",
        activity: "thinking",
        label: "Working",
      };
    } else if (event.type === "tool.failed") {
      yield {
        kind: "progress",
        state: "failed",
        activity: toolActivity(event.tool),
        label: toolLabel(event.tool),
        durationMilliseconds: event.duration_ms ?? undefined,
      };
      yield {
        kind: "progress",
        state: "active",
        activity: "thinking",
        label: "Working",
      };
    } else if (event.type === "response.failed") {
      completed = true;
      yield {
        kind: "assistant",
        state: "failed",
        text: event.message ?? "Atlas AI failed.",
      };
    } else if (event.type === "response.completed" && event.response) {
      completed = true;
      const response = event.response;
      const suggestions = event.suggestions ?? [];
      const content = [
        response.message,
        ...suggestions.map(
          (suggestion) =>
            `${suggestion.title}: ${suggestion.description}`,
        ),
      ].join("\n");
      history.push(
        { role: "user", content: prompt },
        { role: "assistant", content },
      );
      rememberSuggestions(suggestions);
      yield {
        kind: "assistant",
        state: "completed",
        text: response.message,
        suggestions: suggestions.map((suggestion, index) => ({
          index: index + 1,
          title: suggestion.title,
          description: suggestion.description,
        })),
      };
    }
  }
  if (!completed) {
    throw new ConsoleError(
      "Atlas AI closed the response without returning a message.",
    );
  }
}

function selectSuggestion(
  suggestions: AiSqlSuggestion[] | undefined,
  rawIndex: string | undefined,
): AiSqlSuggestion {
  if (!suggestions?.length) {
    throw new ConsoleError(
      "Atlas AI has not suggested SQL in this session.",
    );
  }
  if (rawIndex === undefined) {
    if (suggestions.length === 1) return suggestions[0]!;
    throw new ConsoleError(
      `Choose a suggestion from 1 to ${suggestions.length}.`,
    );
  }
  if (!/^[1-9]\d*$/.test(rawIndex)) {
    throw new ConsoleError("The suggestion number must be a positive integer.");
  }
  const suggestion = suggestions[Number(rawIndex) - 1];
  if (!suggestion) {
    throw new ConsoleError(
      `Suggestion ${rawIndex} does not exist; choose 1 to ${suggestions.length}.`,
    );
  }
  return suggestion;
}

function toolActivity(tool: string | null | undefined): "catalogue" | "sql" {
  return tool?.includes("SQL") ? "sql" : "catalogue";
}

function toolLabel(tool: string | null | undefined): string {
  const label = tool?.trim() || "Use catalogue tool";
  return `${label[0]!.toLocaleUpperCase()}${label.slice(1)}`;
}
