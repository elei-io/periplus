import type {
  CompletionItem,
  CompletionProvider,
  CompletionRequest,
} from "./types.js";

export function choiceCompletion(
  choices: readonly string[],
  kind: CompletionItem["kind"] = "argument",
): CompletionProvider {
  return ({ cursor, prefix, replaceStart }) =>
    choices
      .filter((choice) =>
        choice.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase()),
      )
      .map((choice) => ({
        insertText: choice,
        replaceStart,
        replaceEnd: cursor,
        kind,
      }));
}

export function resourceCompletion<T>({
  load,
  value,
  description,
  kind = "argument",
  cacheMilliseconds = 5_000,
}: {
  load(request: CompletionRequest): Promise<readonly T[]>;
  value(item: T): string;
  description?(item: T): string | undefined;
  kind?: CompletionItem["kind"];
  cacheMilliseconds?: number;
}): CompletionProvider {
  let cached: Promise<readonly T[]> | undefined;
  let cachedAt = 0;
  return async (request) => {
    const now = Date.now();
    if (cached && now - cachedAt >= cacheMilliseconds) cached = undefined;
    if (cacheMilliseconds > 0 && !cached) {
      cachedAt = now;
      cached = load(request).catch((reason) => {
        cached = undefined;
        throw reason;
      });
    }
    const pending = cached ?? load(request);
    const items = await pending;
    const prefix = request.prefix.toLocaleLowerCase();
    return items
      .filter((item) => value(item).toLocaleLowerCase().startsWith(prefix))
      .map((item) => ({
        insertText: value(item),
        replaceStart: request.replaceStart,
        replaceEnd: request.cursor,
        kind,
        description: description?.(item),
      }));
  };
}

export function safeCompletions(
  items: readonly CompletionItem[],
): CompletionItem[] {
  const seen = new Set<string>();
  return items
    .filter((item) => !/[\u0000-\u001f\u007f-\u009f]/.test(item.insertText))
    .filter((item) => {
      const key = `${item.replaceStart}:${item.insertText.toLocaleLowerCase()}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort(
      (left, right) =>
        (right.priority ?? 0) - (left.priority ?? 0) ||
        left.insertText.localeCompare(right.insertText),
    );
}
