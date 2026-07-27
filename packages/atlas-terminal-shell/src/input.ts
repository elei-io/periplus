export class MultilineInput {
  private readonly lines: string[] = []
  private timer: NodeJS.Timeout | undefined

  constructor(
    private readonly submit: (input: string) => void,
    private readonly settleMilliseconds = 20,
  ) {}

  push(line: string): void {
    this.lines.push(line)
    if (this.timer) clearTimeout(this.timer)
    this.timer = setTimeout(() => this.settle(), this.settleMilliseconds)
  }

  clear(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = undefined
    this.lines.length = 0
  }

  close(): void {
    if (this.lines.length > 0) this.flush()
    else this.clear()
  }

  private settle(): void {
    this.timer = undefined
    const input = this.lines.join("\n")
    if (this.lines.length > 1 && !input.trimEnd().endsWith(";")) return
    this.flush()
  }

  private flush(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = undefined
    const input = this.lines.join("\n")
    this.lines.length = 0
    this.submit(input)
  }
}
