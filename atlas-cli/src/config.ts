import { readFile } from "node:fs/promises";
import { dirname, join, parse } from "node:path";

interface AtlasConfiguration {
  api_url?: unknown;
  web_url?: unknown;
}

export async function resolveApiUrl(
  override: string | undefined,
  cwd = process.cwd(),
): Promise<string> {
  return resolveUrl({
    override: override ?? process.env.ATLAS_API_URL,
    key: "api_url",
    fallback: "http://127.0.0.1:8000",
    cwd,
  });
}

export async function resolveWebUrl(
  override: string | undefined,
  cwd = process.cwd(),
): Promise<string> {
  return resolveUrl({
    override: override ?? process.env.ATLAS_WEB_URL,
    key: "web_url",
    fallback: "http://127.0.0.1:8080",
    cwd,
  });
}

async function resolveUrl({
  override,
  key,
  fallback,
  cwd,
}: {
  override: string | undefined;
  key: keyof AtlasConfiguration;
  fallback: string;
  cwd: string;
}): Promise<string> {
  if (override) return override.replace(/\/$/, "");
  let directory = cwd;
  const root = parse(directory).root;
  while (true) {
    const path = join(directory, "atlas.json");
    try {
      const configuration = JSON.parse(
        await readFile(path, "utf8"),
      ) as AtlasConfiguration;
      const value = configuration[key];
      if (typeof value === "string" && value.length) {
        return value.replace(/\/$/, "");
      }
      return fallback;
    } catch (reason) {
      if (
        !(reason instanceof Error) ||
        !("code" in reason) ||
        reason.code !== "ENOENT"
      ) {
        throw reason;
      }
    }
    if (directory === root) break;
    directory = dirname(directory);
  }
  return fallback;
}
