import type {
  Disposable,
  InteractiveTerminal,
} from "@atlas/console-core";

export class NodeTerminal implements InteractiveTerminal {
  private active = false;
  private footerEnabled = false;
  private footerRenderer: (columns: number) => string = () => "";
  private readonly resize = () => this.configureFooter();

  writeRaw(value: string): void {
    process.stdout.write(value);
  }

  columns(): number {
    return process.stdout.columns ?? 100;
  }

  clearScreen(): void {
    this.writeRaw("\u001b[2J\u001b[H");
  }

  enablePinnedFooter(renderer: (columns: number) => string): void {
    this.footerRenderer = renderer;
    if (!this.footerEnabled) {
      this.footerEnabled = true;
      process.stdout.on("resize", this.resize);
    }
    this.configureFooter();
  }

  refreshFooter(): void {
    if (this.footerEnabled) this.drawFooter();
  }

  onData(listener: (data: string) => void): Disposable {
    if (!this.active) {
      process.stdin.setEncoding("utf8");
      process.stdin.setRawMode(true);
      process.stdin.resume();
      this.active = true;
    }
    process.stdin.on("data", listener);
    return {
      dispose: () => process.stdin.off("data", listener),
    };
  }

  close(): void {
    if (this.footerEnabled) {
      process.stdout.off("resize", this.resize);
      this.restoreViewport();
      this.footerEnabled = false;
    }
    if (this.active) {
      process.stdin.setRawMode(false);
      process.stdin.pause();
      this.active = false;
    }
  }

  private configureFooter(): void {
    const rows = process.stdout.rows ?? 0;
    if (!this.footerEnabled || rows < 3) return;
    const bottom = rows - 1;
    this.writeRaw(`\u001b7\u001b[1;${bottom}r\u001b8`);
    this.drawFooter();
  }

  private drawFooter(): void {
    const rows = process.stdout.rows ?? 0;
    if (!this.footerEnabled || rows < 3) return;
    const footer = this.footerRenderer(this.columns());
    this.writeRaw(
      `\u001b7\u001b[${rows};1H\u001b[2K${footer}\u001b8`,
    );
  }

  private restoreViewport(): void {
    const rows = process.stdout.rows ?? 0;
    const clear = rows >= 1 ? `\u001b[${rows};1H\u001b[2K` : "";
    this.writeRaw(`\u001b7${clear}\u001b[r\u001b8`);
  }
}
