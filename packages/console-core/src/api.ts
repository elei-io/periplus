import { ApiError } from "./errors.js";
import type { AtlasApi, CrawlGraphList } from "./types.js";

export class HttpAtlasApi implements AtlasApi {
  readonly graphs = {
    list: (signal?: AbortSignal) =>
      this.get<CrawlGraphList>("/crawl-graphs/", signal),
  };

  constructor(
    private readonly baseUrl: string,
    private readonly request: typeof fetch = globalThis.fetch.bind(globalThis),
  ) {}

  private async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await this.request(
      new URL(path.replace(/^\//, ""), `${this.baseUrl.replace(/\/$/, "")}/`),
      {
        headers: { Accept: "application/json" },
        signal,
      },
    );
    if (!response.ok) {
      const body = await response.json().catch(() => null) as unknown;
      const detail =
        body && typeof body === "object" && "detail" in body
          ? (body as { detail: unknown }).detail
          : body;
      throw new ApiError(
        typeof detail === "string"
          ? detail
          : `Atlas API returned ${response.status}.`,
        response.status,
        detail,
      );
    }
    return response.json() as Promise<T>;
  }
}
