import type { CompletionItem } from "./types.js";

const BLUE = "\u001b[1;34m";
const DIM = "\u001b[2m";
const RESET = "\u001b[0m";

export interface Disposable {
  dispose(): void;
}

export interface InteractiveTerminal {
  writeRaw(value: string): void;
  onData(listener: (data: string) => void): Disposable;
  columns?(): number;
}

export type Completer = (
  input: string,
  cursor: number,
) => Promise<CompletionItem[]>;

export class GhostTextEditor {
  constructor(
    private readonly terminal: InteractiveTerminal,
    private readonly completer: Completer,
    private readonly debounceMilliseconds = 75,
    private readonly history: string[] = [],
    private readonly onInputChanged?: (input: string) => void,
  ) {}

  readLine(prompt: string, initialValue = ""): Promise<string> {
    let buffer = initialValue;
    let cursor = initialValue.length;
    let completions: CompletionItem[] = [];
    let completionIndex = 0;
    let historyIndex = this.history.length;
    let requestVersion = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let continuationStart = 0;

    const currentCompletion = (): CompletionItem | undefined => {
      const completion = completions[completionIndex];
      if (!completion || completion.replaceEnd !== cursor) return undefined;
      const typed = buffer.slice(completion.replaceStart, cursor);
      if (
        !completion.insertText
          .toLocaleLowerCase()
          .startsWith(typed.toLocaleLowerCase())
      ) {
        return undefined;
      }
      return completion;
    };
    const ghostText = () => {
      const completion = currentCompletion();
      if (!completion) return "";
      return completion.insertText.slice(
        cursor - completion.replaceStart,
      );
    };
    const redraw = () => {
      const continuationPrompt = `${" ".repeat(Math.max(0, visibleLength(prompt) - 5))}...> `;
      const activePrompt = continuationStart > 0
        ? continuationPrompt
        : prompt;
      const before = buffer.slice(continuationStart, cursor);
      const after = buffer.slice(cursor);
      const ghost = ghostText();
      const description = ghost
        ? currentCompletion()?.description
        : undefined;
      const occupied =
        visibleLength(activePrompt) +
        visibleLength(before) +
        visibleLength(ghost) +
        visibleLength(after);
      const detail = completionDetail(
        description,
        (this.terminal.columns?.() ?? 100) - occupied - 1,
      );
      this.terminal.writeRaw(
        `\r\u001b[2K${BLUE}${safeText(activePrompt)}${RESET}` +
          `${safeText(before)}${DIM}${safeText(ghost)}${RESET}${safeText(after)}` +
          `${DIM}${detail}${RESET}`,
      );
      const moveLeft =
        visibleLength(ghost) + visibleLength(after) + visibleLength(detail);
      if (moveLeft > 0) this.terminal.writeRaw(`\u001b[${moveLeft}D`);
    };
    const requestCompletions = () => {
      if (timer) clearTimeout(timer);
      completions = [];
      completionIndex = 0;
      const version = ++requestVersion;
      redraw();
      if (!buffer) return;
      timer = setTimeout(async () => {
        const input = buffer;
        const inputCursor = cursor;
        try {
          const results = await this.completer(input, inputCursor);
          if (
            version !== requestVersion ||
            input !== buffer ||
            inputCursor !== cursor
          ) {
            return;
          }
          completions = results.filter((completion) => {
            if (
              completion.replaceEnd !== inputCursor ||
              hasControlCharacters(completion.insertText)
            ) {
              return false;
            }
            const typed = input.slice(
              completion.replaceStart,
              inputCursor,
            );
            return (
              completion.insertText
                .toLocaleLowerCase()
                .startsWith(typed.toLocaleLowerCase()) &&
              completion.insertText.length > typed.length
            );
          });
          redraw();
        } catch {
          if (version === requestVersion) completions = [];
        }
      }, this.debounceMilliseconds);
    };
    const acceptCompletion = () => {
      const completion = currentCompletion();
      if (!completion || !ghostText()) return false;
      buffer =
        buffer.slice(0, completion.replaceStart) +
        completion.insertText +
        buffer.slice(completion.replaceEnd);
      cursor = completion.replaceStart + completion.insertText.length;
      this.onInputChanged?.(buffer);
      requestCompletions();
      return true;
    };

    this.terminal.writeRaw(
      `${BLUE}${safeText(prompt)}${RESET}${safeText(initialValue)}`,
    );
    return new Promise((resolve) => {
      const finish = (value = buffer) => {
        if (timer) clearTimeout(timer);
        completions = [];
        requestVersion += 1;
        subscription.dispose();
        this.onInputChanged?.("");
        redraw();
        this.terminal.writeRaw("\r\n");
        if (value.trim() && this.history.at(-1) !== value) {
          this.history.push(value);
        }
        resolve(value);
      };
      const replaceBuffer = (value: string, position = value.length) => {
        buffer = value;
        cursor = position;
        this.onInputChanged?.(buffer);
        requestCompletions();
      };
      const handleData = (data: string) => {
        if (
          data.length > 1 &&
          /[\r\n]/.test(data) &&
          !data.startsWith("\u001b[")
        ) {
          for (const part of data.match(/[^\r\n]+|\r\n|\r|\n/g) ?? []) {
            handleData(part === "\r\n" ? "\r" : part);
          }
          return;
        }
        if (data === "\r" || data === "\n") {
          if (isCompleteInput(buffer)) {
            finish();
          } else {
            if (timer) clearTimeout(timer);
            completions = [];
            requestVersion += 1;
            redraw();
            buffer =
              buffer.slice(0, cursor) + "\n" + buffer.slice(cursor);
            cursor += 1;
            continuationStart = cursor;
            this.onInputChanged?.(buffer);
            this.terminal.writeRaw("\r\n");
            requestCompletions();
          }
        } else if (
          data === "\t" ||
          (data === "\u001b[C" && cursor === buffer.length)
        ) {
          if (!acceptCompletion() && cursor < buffer.length) {
            cursor += 1;
            requestCompletions();
          }
        } else if (data === "\u001b[Z") {
          if (completions.length > 1) {
            completionIndex = (completionIndex + 1) % completions.length;
            redraw();
          }
        } else if (data === "\u001b[D") {
          if (cursor > 0) cursor -= 1;
          requestCompletions();
        } else if (data === "\u001b[C") {
          if (cursor < buffer.length) cursor += 1;
          requestCompletions();
        } else if (data === "\u007f") {
          if (cursor > 0) {
            replaceBuffer(
              buffer.slice(0, cursor - 1) + buffer.slice(cursor),
              cursor - 1,
            );
          }
        } else if (data === "\u001b[3~") {
          if (cursor < buffer.length) {
            replaceBuffer(
              buffer.slice(0, cursor) + buffer.slice(cursor + 1),
              cursor,
            );
          }
        } else if (data === "\u001b[A" || data === "\u001b[B") {
          const direction = data === "\u001b[A" ? -1 : 1;
          historyIndex = Math.max(
            0,
            Math.min(this.history.length, historyIndex + direction),
          );
          continuationStart = 0;
          replaceBuffer(this.history[historyIndex] ?? "");
        } else if (data === "\u0003") {
          buffer = "";
          this.terminal.writeRaw("^C");
          finish("");
        } else if (data === "\u0004" && !buffer) {
          finish(".exit");
        } else if (data === "\u000c") {
          this.terminal.writeRaw("\u001b[2J\u001b[H");
          redraw();
        } else if (data === "\u001b") {
          completions = [];
          requestVersion += 1;
          redraw();
        } else if (!hasControlCharacters(data)) {
          replaceBuffer(
            buffer.slice(0, cursor) + data + buffer.slice(cursor),
            cursor + data.length,
          );
        }
      };
      const subscription = this.terminal.onData(handleData);
      this.onInputChanged?.(buffer);
    });
  }
}

function hasControlCharacters(value: string): boolean {
  return /[\u0000-\u001f\u007f-\u009f]/.test(value);
}

function safeText(value: string): string {
  return value
    .replace(/\r\n|\r|\n/g, " ")
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, "");
}

function visibleLength(value: string): number {
  return [...value].length;
}

function completionDetail(
  description: string | undefined,
  availableColumns: number,
): string {
  const prefix = "  · ";
  const available = availableColumns - visibleLength(prefix);
  if (!description || available < 12) return "";
  const value = safeText(description);
  const truncated =
    visibleLength(value) <= available
      ? value
      : `${[...value].slice(0, available - 1).join("")}…`;
  return `${prefix}${truncated}`;
}

function isCompleteInput(value: string): boolean {
  const input = value.trim();
  return !input || input.startsWith(".") || input.endsWith(";");
}
