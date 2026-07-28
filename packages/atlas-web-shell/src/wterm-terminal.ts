import type { WTerm } from "@wterm/dom"

export class WtermTerminal {
  private instance?: WTerm
  private columnCount = 80

  attach(instance: WTerm): void {
    this.instance = instance
    this.columnCount = instance.cols
  }

  detach(): void {
    this.instance = undefined
  }

  write(value: string): void {
    this.instance?.write(value)
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
}
