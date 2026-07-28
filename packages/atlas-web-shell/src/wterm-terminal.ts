import type {
  Disposable,
  InteractiveTerminal,
} from "atlas-console-core"
import type { WTerm } from "@wterm/dom"

export class WtermTerminal implements InteractiveTerminal {
  private instance?: WTerm
  private columnCount = 80
  private readonly listeners = new Set<(data: string) => void>()

  attach(instance: WTerm): void {
    this.instance = instance
    this.columnCount = instance.cols
  }

  detach(): void {
    this.instance = undefined
  }

  writeRaw(value: string): void {
    this.instance?.write(value)
  }

  receive(value: string): void {
    for (const listener of [...this.listeners]) listener(value)
  }

  onData(listener: (data: string) => void): Disposable {
    this.listeners.add(listener)
    return { dispose: () => this.listeners.delete(listener) }
  }

  resized(columns: number): void {
    this.columnCount = columns
  }

  columns(): number {
    return this.columnCount
  }

  focus(): void {
    this.instance?.focus()
  }

  async copyText(value: string): Promise<void> {
    await navigator.clipboard.writeText(value)
  }
}
