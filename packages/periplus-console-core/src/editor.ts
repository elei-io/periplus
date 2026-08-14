import type { Completion } from "./types.js"
import { sanitizeTerminalText } from "./format.js"

const BLUE = "\u001b[1;34m"
const DIM = "\u001b[2m"
const RESET = "\u001b[0m"

export interface Disposable {
  dispose(): void
}

export interface InteractiveTerminal {
  writeRaw(value: string): void
  onData(listener: (data: string) => void): Disposable
  columns?(): number
  copyText?(value: string): Promise<void>
}

export type Completer = (
  input: string,
  cursor: number,
) => Promise<Completion[]>

export class GhostTextEditor {
  constructor(
    private readonly terminal: InteractiveTerminal,
    private readonly completer: Completer,
    private readonly debounceMilliseconds = 75,
    private readonly history: string[] = [],
  ) {}

  readLine(prompt: string, initialValue = ""): Promise<string> {
    let buffer = initialValue
    let cursor = initialValue.length
    let completions: Completion[] = []
    let completionIndex = 0
    let historyIndex = this.history.length
    let requestVersion = 0
    let timer: ReturnType<typeof setTimeout> | undefined

    const currentCompletion = (): Completion | undefined => {
      const completion = completions[completionIndex]
      if (!completion || completion.replaceEnd !== cursor) return undefined
      const typed = buffer.slice(completion.replaceStart, cursor)
      if (
        !completion.value
          .toLowerCase()
          .startsWith(typed.toLowerCase())
      ) {
        return undefined
      }
      return completion
    }
    const ghostText = () => {
      const completion = currentCompletion()
      if (!completion) return ""
      return completion.value.slice(cursor - completion.replaceStart)
    }
    const redraw = () => {
      const lineStart = buffer.lastIndexOf("\n", Math.max(0, cursor - 1)) + 1
      const followingBreak = buffer.indexOf("\n", cursor)
      const lineEnd = followingBreak < 0 ? buffer.length : followingBreak
      const continuationPrompt =
        `${" ".repeat(Math.max(0, visibleLength(prompt) - 5))}...> `
      const activePrompt = lineStart > 0 ? continuationPrompt : prompt
      const before = buffer.slice(lineStart, cursor)
      const after = buffer.slice(cursor, lineEnd)
      const ghost = ghostText()
      const viewport = inlineViewport(
        before,
        ghost,
        after,
        Math.max(
          8,
          (this.terminal.columns?.() ?? 100) -
            visibleLength(activePrompt) -
            1,
        ),
      )
      const unclipped =
        !viewport.leftClipped && !viewport.rightClipped
      const description =
        ghost && unclipped ? currentCompletion()?.description : undefined
      const occupied =
        visibleLength(activePrompt) +
        visibleLength(viewport.before) +
        visibleLength(viewport.ghost) +
        visibleLength(viewport.after) +
        (viewport.leftClipped ? 1 : 0) +
        (viewport.rightClipped ? 1 : 0)
      const detail = completionDetail(
        description,
        (this.terminal.columns?.() ?? 100) - occupied - 1,
      )
      this.terminal.writeRaw(
        `\r\u001b[2K${BLUE}${safeInline(activePrompt)}${RESET}` +
          `${viewport.leftClipped ? `${DIM}…${RESET}` : ""}` +
          `${safeInline(viewport.before)}` +
          `${DIM}${safeInline(viewport.ghost)}${RESET}` +
          `${safeInline(viewport.after)}` +
          `${viewport.rightClipped ? `${DIM}…${RESET}` : ""}` +
          `${DIM}${detail}${RESET}`,
      )
      const moveLeft =
        visibleLength(viewport.ghost) +
        visibleLength(viewport.after) +
        (viewport.rightClipped ? 1 : 0) +
        visibleLength(detail)
      if (moveLeft > 0) this.terminal.writeRaw(`\u001b[${moveLeft}D`)
    }
    const requestCompletions = () => {
      if (timer) clearTimeout(timer)
      completions = []
      completionIndex = 0
      const version = ++requestVersion
      redraw()
      if (!buffer) return
      timer = setTimeout(async () => {
        const input = buffer
        const inputCursor = cursor
        try {
          const results = await this.completer(input, inputCursor)
          if (
            version !== requestVersion ||
            input !== buffer ||
            inputCursor !== cursor
          ) {
            return
          }
          completions = results.filter((completion) => {
            if (
              completion.replaceEnd !== inputCursor ||
              hasControlCharacters(completion.value)
            ) {
              return false
            }
            const typed = input.slice(completion.replaceStart, inputCursor)
            return (
              completion.value.toLowerCase().startsWith(typed.toLowerCase()) &&
              completion.value.length > typed.length
            )
          })
          redraw()
        } catch {
          if (version === requestVersion) completions = []
        }
      }, this.debounceMilliseconds)
    }
    const acceptCompletion = () => {
      const completion = currentCompletion()
      if (!completion || !ghostText()) return false
      buffer =
        buffer.slice(0, completion.replaceStart) +
        completion.value +
        buffer.slice(completion.replaceEnd)
      cursor = completion.replaceStart + completion.value.length
      requestCompletions()
      return true
    }

    return new Promise((resolve) => {
      const finish = (value = buffer) => {
        if (timer) clearTimeout(timer)
        completions = []
        requestVersion += 1
        subscription.dispose()
        redraw()
        this.terminal.writeRaw("\r\n")
        if (value.trim() && this.history.at(-1) !== value.trim()) {
          this.history.push(value.trim())
          if (this.history.length > 100) this.history.shift()
        }
        resolve(value)
      }
      const replaceBuffer = (value: string, position = value.length) => {
        buffer = value
        cursor = position
        requestCompletions()
      }
      const handleData = (data: string) => {
        if (
          data.length > 1 &&
          /[\r\n]/.test(data) &&
          !data.startsWith("\u001b[")
        ) {
          for (const part of data.match(/[^\r\n]+|\r\n|\r|\n/g) ?? []) {
            handleData(part === "\r\n" ? "\r" : part)
          }
          return
        }
        if (data === "\r" || data === "\n") {
          if (isCompleteInput(buffer)) {
            finish()
          } else {
            if (timer) clearTimeout(timer)
            completions = []
            requestVersion += 1
            redraw()
            buffer = buffer.slice(0, cursor) + "\n" + buffer.slice(cursor)
            cursor += 1
            this.terminal.writeRaw("\r\n")
            requestCompletions()
          }
        } else if (
          data === "\t" ||
          (data === "\u001b[C" && cursor === buffer.length)
        ) {
          if (!acceptCompletion() && cursor < buffer.length) {
            cursor += 1
            requestCompletions()
          }
        } else if (data === "\u001b[Z") {
          if (completions.length > 1) {
            completionIndex = (completionIndex + 1) % completions.length
            redraw()
          }
        } else if (data === "\u001b[D") {
          if (cursor > 0) cursor -= 1
          requestCompletions()
        } else if (data === "\u001b[C") {
          if (cursor < buffer.length) cursor += 1
          requestCompletions()
        } else if (data === "\u007f") {
          if (cursor > 0) {
            replaceBuffer(
              buffer.slice(0, cursor - 1) + buffer.slice(cursor),
              cursor - 1,
            )
          }
        } else if (data === "\u001b[3~") {
          if (cursor < buffer.length) {
            replaceBuffer(
              buffer.slice(0, cursor) + buffer.slice(cursor + 1),
              cursor,
            )
          }
        } else if (data === "\u001b[A" || data === "\u001b[B") {
          const direction = data === "\u001b[A" ? -1 : 1
          historyIndex = Math.max(
            0,
            Math.min(this.history.length, historyIndex + direction),
          )
          replaceBuffer(this.history[historyIndex] ?? "")
        } else if (data === "\u0003") {
          buffer = ""
          this.terminal.writeRaw("^C")
          finish("")
        } else if (data === "\u0004" && !buffer) {
          finish(".exit")
        } else if (data === "\u000c") {
          this.terminal.writeRaw("\u001b[2J\u001b[H")
          redraw()
        } else if (data === "\u001b") {
          completions = []
          requestVersion += 1
          redraw()
        } else if (!hasControlCharacters(data)) {
          replaceBuffer(
            buffer.slice(0, cursor) + data + buffer.slice(cursor),
            cursor + data.length,
          )
        }
      }
      const subscription = this.terminal.onData(handleData)
      requestCompletions()
    })
  }
}

function inlineViewport(
  before: string,
  ghost: string,
  after: string,
  width: number,
): {
  before: string
  ghost: string
  after: string
  leftClipped: boolean
  rightClipped: boolean
} {
  const total = visibleLength(before) + visibleLength(ghost) + visibleLength(after)
  if (total <= width) {
    return {
      before,
      ghost,
      after,
      leftClipped: false,
      rightClipped: false,
    }
  }

  const tail = ghost + after
  const tailBudget = Math.min(
    visibleLength(tail),
    Math.max(2, Math.floor(width / 3)),
  )
  const beforeBudget = Math.max(2, width - tailBudget - 1)
  const beforeCharacters = [...before]
  const leftClipped = beforeCharacters.length > beforeBudget
  const visibleBefore = leftClipped
    ? beforeCharacters.slice(-beforeBudget).join("")
    : before
  const remainingForTail =
    width - visibleLength(visibleBefore) - (leftClipped ? 1 : 0)
  const rightClipped = visibleLength(tail) > remainingForTail
  const remaining = Math.max(
    0,
    remainingForTail - (rightClipped ? 1 : 0),
  )
  const ghostCharacters = [...ghost]
  const visibleGhost = ghostCharacters.slice(0, remaining).join("")
  const afterBudget = Math.max(0, remaining - visibleLength(visibleGhost))
  const afterCharacters = [...after]
  const visibleAfter = afterCharacters.slice(0, afterBudget).join("")

  return {
    before: visibleBefore,
    ghost: visibleGhost,
    after: visibleAfter,
    leftClipped,
    rightClipped,
  }
}

function hasControlCharacters(value: string): boolean {
  return /[\u0000-\u001f\u007f-\u009f]/.test(value)
}

function safeInline(value: string): string {
  return sanitizeTerminalText(value).replace(/\r\n|\r|\n/g, " ")
}

function visibleLength(value: string): number {
  return [...value].length
}

function completionDetail(
  description: string | undefined,
  availableColumns: number,
): string {
  const prefix = "  · "
  const available = availableColumns - visibleLength(prefix)
  if (!description || available < 12) return ""
  const value = safeInline(description)
  const truncated =
    visibleLength(value) <= available
      ? value
      : `${[...value].slice(0, available - 1).join("")}…`
  return `${prefix}${truncated}`
}

function isCompleteInput(value: string): boolean {
  const input = value.trim()
  return !input || input.startsWith(".") || input.endsWith(";")
}
