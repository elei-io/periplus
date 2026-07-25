import type {
  AtlasApi,
  CatalogueCompilationDiagnostic,
} from "./types.js";

export type ConnectionStatus =
  | { state: "connecting" }
  | { state: "connected" }
  | { state: "disconnected"; message?: string };

export type CompilerStatus =
  | { state: "idle" }
  | { state: "debouncing" }
  | { state: "checking" }
  | { state: "valid" }
  | { state: "optimized"; rewriteCount: number }
  | {
      state: "diagnostics";
      diagnostics: CatalogueCompilationDiagnostic[];
    }
  | { state: "unavailable"; message?: string };

export interface ConsoleStatus {
  connection: ConnectionStatus;
  lakeSlug?: string;
  schemaVersion?: string;
  compilerVersion?: string;
  compiler: CompilerStatus;
}

export interface StatusSource {
  snapshot(): ConsoleStatus;
  subscribe(listener: () => void): { dispose(): void };
  updateInput(input: string): void;
}

export class LiveConsoleStatus implements StatusSource {
  private value: ConsoleStatus = {
    connection: { state: "connecting" },
    compiler: { state: "idle" },
  };
  private readonly listeners = new Set<() => void>();
  private timer?: ReturnType<typeof setTimeout>;
  private compilation?: AbortController;
  private requestVersion = 0;

  constructor(
    private readonly api: AtlasApi,
    private readonly debounceMilliseconds = 700,
  ) {}

  snapshot(): ConsoleStatus {
    return this.value;
  }

  subscribe(listener: () => void): { dispose(): void } {
    this.listeners.add(listener);
    return { dispose: () => this.listeners.delete(listener) };
  }

  async connect(signal?: AbortSignal): Promise<void> {
    this.patch({ connection: { state: "connecting" } });
    try {
      const status = await this.api.catalogue.status(signal);
      this.patch({
        connection: { state: "connected" },
        lakeSlug: status.lake_slug,
        schemaVersion: status.catalogue_schema_version,
        compilerVersion: status.compiler_version,
      });
    } catch (reason) {
      this.patch({
        connection: {
          state: "disconnected",
          message: errorMessage(reason),
        },
      });
    }
  }

  updateInput(input: string): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
    this.compilation?.abort();
    this.compilation = undefined;
    const version = ++this.requestVersion;
    const sql = input.trim();
    if (!sql || sql.startsWith(".")) {
      this.patch({ compiler: { state: "idle" } });
      return;
    }

    this.patch({ compiler: { state: "debouncing" } });
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.compile(sql, version);
    }, this.debounceMilliseconds);
  }

  dispose(): void {
    if (this.timer) clearTimeout(this.timer);
    this.compilation?.abort();
    this.listeners.clear();
  }

  private async compile(sql: string, version: number): Promise<void> {
    const controller = new AbortController();
    this.compilation = controller;
    this.patch({ compiler: { state: "checking" } });
    try {
      const result = await this.api.catalogue.compile(sql, controller.signal);
      if (version !== this.requestVersion) return;
      const diagnostics = result.diagnostics.filter(
        (item) => item.severity === "warning" || item.severity === "error",
      );
      this.patch({
        connection: { state: "connected" },
        compiler:
          diagnostics.length > 0 || !result.valid
            ? { state: "diagnostics", diagnostics }
            : result.outcome === "optimized"
              ? {
                  state: "optimized",
                  rewriteCount: result.applied_rewrites.length,
                }
              : { state: "valid" },
      });
    } catch (reason) {
      if (controller.signal.aborted || version !== this.requestVersion) return;
      this.patch({
        compiler: { state: "unavailable", message: errorMessage(reason) },
      });
    } finally {
      if (this.compilation === controller) this.compilation = undefined;
    }
  }

  private patch(change: Partial<ConsoleStatus>): void {
    this.value = { ...this.value, ...change };
    for (const listener of this.listeners) listener();
  }
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
